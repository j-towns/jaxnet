from collections import namedtuple, Counter, defaultdict
from pathlib import Path

import dill
import jax
import numpy as onp
from jax import lax, random, core as jc, linear_util as lu, \
    unzip2, unzip3, safe_zip, safe_map, partial, WrapHashably, tree_util, api_util, split_list, \
    raise_to_shaped, curry
from jax.abstract_arrays import ShapedArray
from jax.interpreters import xla, partial_eval as pe
from jax.interpreters.partial_eval import trace_to_jaxpr, PartialVal
from jax.lax.lax_control_flow import _index_array, _update_array, _empty_array, fori_loop
from jax.random import PRNGKey
from jax.util import split_dict

zip = safe_zip
map = safe_map


def _get_primitive_init(primitive):
    if primitive in init_rules:
        return partial(init_rules[primitive])

    if isinstance(primitive, parametrized):
        return _parametrized_init(primitive)

    return _default_init(primitive)


@curry
def _default_init(primitive, rng, parameters_dict, *in_vals, **kwargs):
    return primitive.bind(*in_vals, **kwargs), parameters_dict


@curry
def _parametrized_init(parametrized, rng, parameters_dict, *inputs):
    # TODO check on all nesting levels, not just parent:
    if parametrized not in parameters_dict:
        parameters_dict[parametrized] = parametrized._init_parameters_dict(rng, *inputs)
    parameters = parametrized._parameters_namedtuple(parameters_dict[parametrized])
    out, _ = jax.tree_flatten(parametrized.apply(parameters, *inputs))
    return out, parameters_dict


def _call_init(rng, parameters_dict, kwargs, jaxpr, consts, freevar_vals, in_vals):
    jaxpr, = jaxpr
    consts, = consts
    freevar_vals, = freevar_vals
    f = lu.wrap_init(partial(jc.eval_jaxpr, jaxpr, consts, freevar_vals))
    return xla.xla_call_p.bind(f, *in_vals, **kwargs), parameters_dict


def _parametrized_scan_impl(cell_parameters, *args, **kwargs):
    """Almost identical to lax_control_flow._scan_impl, but allowing for a parametrized cell."""

    forward, length, num_consts, num_carry, jaxpr, linear = split_dict(
        kwargs, ["forward", "length", "num_consts", "num_carry", "jaxpr", "linear"])

    consts, init, xs = split_list(args, [num_consts, num_carry])
    _, _, x_avals = split_list(jaxpr.in_avals, [num_consts, num_carry])
    _, y_avals = split_list(jaxpr.out_avals, [num_carry])

    cell_primitive = parametrized(jc.jaxpr_as_fun(jaxpr))
    cell = partial(cell_primitive.apply, cell_parameters)

    def body_fun(i, vals):
        i = i if forward else length - i - 1
        carry, ys = split_list(vals, [num_carry])
        x = map(partial(_index_array, i), x_avals, xs)
        # difference to _scan_impl: 'cell' instead of 'jc.jaxpr_as_fun(jaxpr)':
        out_flat = cell(*(consts + carry + x))
        carry_out, y_updates = split_list(out_flat, [num_carry])
        ys_out = map(partial(_update_array, i), y_avals, ys, y_updates)
        return carry_out + ys_out

    ys_init = map(partial(_empty_array, length), y_avals)
    return fori_loop(0, length, body_fun, init + ys_init)


def _scan_init(rng, parameters_dict, *args, **kwargs):
    jaxpr = kwargs['jaxpr']
    split_sizes = [kwargs['num_consts'], kwargs['num_carry']]
    consts, init, xs = split_list(args, split_sizes)
    _, _, x_avals = split_list(jaxpr.in_avals, split_sizes)
    x = map(partial(_index_array, 0), x_avals, xs)
    parameters_dict = _get_parameters_dict(rng, jaxpr.jaxpr, jaxpr.literals, (),
                                           parameters_dict, *(consts + init + x))

    def get_cell_params():
        if len(parameters_dict) == 0:
            return ()

        primitive, = parameters_dict.keys()
        return primitive._parameters_namedtuple(parameters_dict[primitive]),

    return _parametrized_scan_impl(get_cell_params(), *args, **kwargs), parameters_dict


def _scan_apply(submodule_parameter_iter, *args, **kwargs):
    # TODO fix param sharing
    cell_params = submodule_parameter_iter.get_parameters_or_empty()
    return _parametrized_scan_impl(cell_params, *args, **kwargs)


def _get_parameters_dict(rng, jaxpr, consts, freevar_vals, parameters_dict, *args):
    def read(v):
        return v.val if type(v) is jc.Literal else env[v]

    def write(v, val):
        env[v] = val

    env = {}
    write(jc.unitvar, jc.unit)
    map(write, jaxpr.constvars, consts)
    map(write, jaxpr.invars, args)
    map(write, jaxpr.freevars, freevar_vals)
    for eqn in jaxpr.eqns:
        rng, prim_rng = random.split(rng)
        in_vals = map(read, eqn.invars)

        primitive_init = _get_primitive_init(eqn.primitive)
        if eqn.bound_subjaxprs:
            subjaxprs, sub_consts, sub_freevar_vals = unzip3([(
                subjaxpr,
                map(read, const_vars),
                map(read, bound_vars))
                for subjaxpr, const_vars, bound_vars
                in eqn.bound_subjaxprs])
            ans, parameters_dict = primitive_init(prim_rng, parameters_dict, eqn.params,
                                                  subjaxprs, sub_consts, sub_freevar_vals, in_vals)
        else:
            ans, parameters_dict = primitive_init(prim_rng, parameters_dict, *in_vals, **eqn.params)

        ans = ans if eqn.primitive.multiple_results else (ans,)
        map(write, eqn.outvars, ans)

    return parameters_dict


init_rules = {xla.xla_call_p: _call_init,
              lax.scan_p: _scan_init}

apply_rules = {lax.scan_p: _scan_apply}


@lu.transformation
def _apply_with_trace(master, parameters, *vals):
    """Transforms a parametrized function into its corresponding apply function."""
    trace = ApplyTrace(master, jc.cur_sublevel())
    outs = yield map(partial(ApplyTracer, trace,
                             ApplyParametersIterator(parameters.val)), vals), {}
    out_tracers = map(trace.full_raise, outs)
    yield [t.val for t in out_tracers]


class ApplyTrace(jc.Trace):
    """Trace used to transform a module function into its corresponding apply function."""

    def pure(self, val):
        return ApplyTracer(self, None, val)

    def lift(self, val):
        return ApplyTracer(self, None, val)

    def sublift(self, val):
        return ApplyTracer(self, None, val.val)

    def process_primitive(self, primitive, tracers, parameters):
        """Processes a call of a primitive during 'apply' of a parametrized function."""
        flat_inputs, parameter_iter = ApplyTracer.merge(tracers)
        if primitive in apply_rules:
            out = apply_rules[primitive](parameter_iter, *flat_inputs, **parameters)
        elif isinstance(primitive, parametrized):
            out = primitive.apply(parameter_iter.get_parameters(primitive), *flat_inputs)
            out, _ = jax.tree_flatten(out)
        else:
            out = primitive.bind(*flat_inputs, **parameters)

        to_tracer = lambda out: ApplyTracer(self, parameter_iter, out)
        return map(to_tracer, out) if primitive.multiple_results else to_tracer(out)

    def process_call(self, call_primitive, f, tracers, parameters):
        """Processes an xla_call during 'apply' of a parametrized function."""
        flat_inputs, parameters_iter = ApplyTracer.merge(tracers)
        f = _apply_with_trace(f, self.master, WrapHashably(parameters_iter))
        flat_outs = call_primitive.bind(f, *flat_inputs, **parameters)
        return map(partial(ApplyTracer, self, parameters_iter), flat_outs)


class ApplyParametersIterator:
    """Allows supplying submodules with their respective parameters while calling a module's `apply`
    function by iterating through the given parameters."""

    def __init__(self, parameters):
        self.parameters = parameters
        self.index = 0
        self.parameters_by_primitive = {}

    def get_parameters(self, primitive):
        parameters = self.parameters_by_primitive.get(primitive)
        if parameters is not None:
            return parameters

        parameters = self.parameters[self.index]
        self.index += 1
        self.parameters_by_primitive[primitive] = parameters
        return parameters

    def get_parameters_or_empty(self):
        return (self.get_parameters(None),) if len(self.parameters) > 0 else ()


class ApplyTracer(jc.Tracer):
    """Tracer used to transform a module function into its corresponding apply function."""
    __slots__ = ['val', 'parameters_iter']

    def __init__(self, trace, parameters_iter, val):
        super().__init__(trace)
        self.val = val
        self.parameters_iter = parameters_iter

    @property
    def aval(self):
        return jc.get_aval(self.val)

    def full_lower(self):
        return self

    @staticmethod
    def merge(tracers):
        flat_inputs, parameters_iters = unzip2((t.val, t.parameters_iter) for t in tracers)

        parameters_iter = None
        for iter in parameters_iters:
            if iter is not None:
                assert parameters_iter is None or iter is parameters_iter
                assert isinstance(iter, ApplyParametersIterator)
                parameters_iter = iter

        return flat_inputs, parameters_iter


class parametrized(jc.Primitive):
    def __init__(self, fun, name=None):
        self._name = name if name else fun.__name__
        self._wrapped_fun = lu.wrap_init(fun) if fun else None
        self.multiple_results = True

        super().__init__(f'{self._name}_{id(self)}')

        @lu.wrap_init
        def init_and_apply(rng, *inputs):
            parameters = self.init_parameters(rng, *inputs)
            return self.apply(parameters, *inputs)

        self._init_and_apply = init_and_apply
        # Avoids running trace_to_jaxpr twice during initialization just for out_tree:
        self._cached_out_tree = None

        def abstract_call(*inputs):
            key_and_inputs = (ShapedArray((2,), 'uint32'),) + inputs
            flat_rng_and_inputs, in_tree_with_rng = jax.tree_flatten(key_and_inputs)
            flat_fun, self._cached_out_tree = jax.flatten_fun_nokwargs(self._init_and_apply,
                                                                       in_tree_with_rng)
            flat_partial_inputs = [PartialVal((a, jc.unit)) for a in flat_rng_and_inputs]
            _, flat_partial_outs, _ = trace_to_jaxpr(
                flat_fun, flat_partial_inputs, instantiate=True)
            flat_outs, _ = unzip2(flat_partial_outs)
            return flat_outs

        self.def_abstract_eval(abstract_call)

    dummy_rng = PRNGKey(0)

    def _out_tree(self, *inputs):
        if self._cached_out_tree is not None:
            result = self._cached_out_tree()
            self._cached_out_tree = None
            return result

        flat_rng_and_inputs, in_tree_with_rng = jax.tree_flatten((parametrized.dummy_rng,) + inputs)
        flat_fun, out_tree = jax.flatten_fun_nokwargs(self._init_and_apply, in_tree_with_rng)
        # Need to abstract eval in order to build out tree:
        pe.trace_to_jaxpr(flat_fun, parametrized._partialize(flat_rng_and_inputs), instantiate=True)
        return out_tree()

    def __call__(self, *inputs):
        parametrized._submodule_call_order_tracing.trace(self)
        flat_inputs, _ = jax.tree_flatten(inputs)
        flat_outs = self.bind(*flat_inputs)
        return jax.tree_unflatten(self._out_tree(*inputs), flat_outs)

    def apply(self, parameters, *inputs, jit=False):
        def _apply(parameters, *inputs):
            def inner():
                flat_inputs, in_tree = tree_util.tree_flatten(inputs)
                flat_fun, out_tree = api_util.flatten_fun_nokwargs(self._wrapped_fun, in_tree)
                with jc.new_master(ApplyTrace) as master:
                    flat_fun = _apply_with_trace(flat_fun, master, WrapHashably(parameters))
                    flat_outputs = flat_fun.call_wrapped(*inputs)
                    del master
                return tree_util.tree_unflatten(out_tree(), flat_outputs)

            return parametrized._submodule_call_order_tracing.nested(self, inner)

        return (jax.jit(_apply) if jit else _apply)(parameters, *inputs)

    def init_parameters(self, rng, *example_inputs, reuse=None):
        return self._init_parameters(rng, *example_inputs, reuse=reuse, reuse_only=False)

    def parameters_from(self, reuse, *example_inputs):
        # TODO https://github.com/JuliusKunze/jaxnet/issues/8
        return self._init_parameters(PRNGKey(0), *example_inputs, reuse=reuse, reuse_only=True)

    def apply_from(self, reuse, *example_inputs, jit=False):
        parameters = self.parameters_from(reuse, *example_inputs)
        return (jax.jit(self.apply) if jit else self.apply)(parameters, *example_inputs)

    def _init_parameters(self, rng, *example_inputs, reuse, reuse_only):
        d = self._init_parameters_dict(rng, *example_inputs)

        if reuse:
            flat_reuse_dicts = parametrized._flat_reuse_dicts(reuse, *example_inputs)
            d = self._merge_reuse_into(d, flat_reuse_dicts, reuse_only=reuse_only)

        return self._parameters_namedtuple(d)

    def _init_parameters_dict(self, rng, *example_inputs):
        flat_inputs, in_tree = tree_util.tree_flatten(example_inputs)
        flat_fun, _ = api_util.flatten_fun_nokwargs(self._wrapped_fun, in_tree)
        (jaxpr, _, consts), submodules_in_call_order = \
            parametrized._submodule_call_order_tracing.nested(
                self, lambda: pe.trace_to_jaxpr(flat_fun, parametrized._partialize(flat_inputs)),
                do_trace_submodules=True)

        parameters_dict = _get_parameters_dict(rng, jaxpr, consts, [], dict(), *example_inputs)

        if len(parameters_dict) <= 1:
            return parameters_dict

        assert len(parameters_dict) == len(submodules_in_call_order)

        permutation = parametrized._permutation_to_jaxpr_order(jaxpr, submodules_in_call_order)
        assert len(parameters_dict) == len(permutation)
        submodule_param_pairs_in_call_order = list(parameters_dict.items())
        submodule_param_pairs_in_jaxpr_order = list(submodule_param_pairs_in_call_order[i]
                                                    for i in permutation)
        return dict(submodule_param_pairs_in_jaxpr_order)

    @staticmethod
    def _flat_reuse_dicts(reuse, *example_inputs):
        r = {}

        for module, parameters in reuse.items():
            inputs = example_inputs
            if isinstance(module, ShapedParametrized):
                module, inputs = module.parametrized, module.example_inputs

            if not isinstance(module, parametrized):
                raise ValueError('Keys for reuse must be parametrized or ShapedParametrized.')

            params_dict = parametrized._parameters_dict(
                parameters, module._init_parameters_dict(PRNGKey(0), *inputs))
            r.update(module._flatten_dict(params_dict))

        return r

    def _merge_reuse_into(self, parameters_dict, flat_reuse_dicts, reuse_only, is_reused=False):
        reused_dict = flat_reuse_dicts.get(self)
        is_reused = reused_dict is not None or is_reused
        parameters_dict = reused_dict if is_reused else parameters_dict

        if not isinstance(parameters_dict, dict):
            if reuse_only and not is_reused:
                raise ValueError(f'No param value specified for {self}.')

            return parameters_dict

        r = {}
        for module, params_d in parameters_dict.items():
            r[module] = module._merge_reuse_into(params_d, flat_reuse_dicts, reuse_only, is_reused)

        return r

    def _flatten_dict(self, param_dict):
        d = {self: param_dict}

        if not isinstance(param_dict, dict):
            return d

        for module, parameters in param_dict.items():
            d.update(module._flatten_dict(parameters))

        return d

    def __str__(self):
        return self.name

    def _parameters_namedtuple(self, parameters_dict):
        if not isinstance(parameters_dict, dict):
            return parameters_dict

        index_by_prefix = defaultdict(lambda: 0)

        prefix_param_pairs = [(module._name, module._parameters_namedtuple(parameters))
                              for module, parameters in parameters_dict.items()]

        prefix_counter = Counter([prefix for prefix, _ in prefix_param_pairs])

        def next_name(prefix):
            is_duplicate = prefix_counter[prefix] > 1
            index = index_by_prefix[prefix]
            name = prefix + str(index if is_duplicate else '')
            index_by_prefix[prefix] = index + 1
            return name

        params = dict((next_name(prefix), params) for prefix, params in prefix_param_pairs)
        Parameters = namedtuple(self._name, params.keys())
        return Parameters(**params)

    @staticmethod
    def _parameters_dict(parameters, example_parameters_dict):
        if not isinstance(parameters, tuple):
            return parameters

        return {submodule: parametrized._parameters_dict(params, submodule_example_parameters_dict)
                for (submodule, submodule_example_parameters_dict), params
                in zip(example_parameters_dict.items(), parameters)}

    def __eq__(self, obj):
        return isinstance(obj, parametrized) and self.name == obj.name

    def __hash__(self):
        return hash(self.name)

    def shaped(self, *inputs):
        return ShapedParametrized(self, *inputs)

    class SubmoduleCallOrderTracing:
        """Used to trace submodule call order during trace_to_jaxpr."""

        def __init__(self):
            self._submodules_by_module = []

        def nested(self, primitive, body, do_trace_submodules=False):
            submodules_init = dict() if do_trace_submodules else None
            self._submodules_by_module.append((primitive, submodules_init))

            try:
                result = body()
            finally:
                module, submodules = self._submodules_by_module.pop()

            assert module == primitive
            return (result, list(submodules.keys())) if do_trace_submodules else result

        def trace(self, submodule):
            _, submodules = self._submodules_by_module[-1]
            do_trace_submodules = submodules is not None
            if not do_trace_submodules:
                return

            if submodule not in submodules:
                # used as ordered set:
                submodules[submodule] = None

    _submodule_call_order_tracing = SubmoduleCallOrderTracing()

    @staticmethod
    def inverse_permutation(permutation):
        return onp.arange(len(permutation))[onp.argsort(permutation)]

    @staticmethod
    def _permutation_to_jaxpr_order(jaxpr, submodules_in_call_order):
        """
        Needed to supply parameter values (in order of appearance in jaxpr)
        to the corresponding submodules (in call order).
        This is done by reordering submodules from call order to jaxpr order.
        """
        permutation = []
        submodule_execution_index_by_name = {submodule.name: index for index, submodule in
                                             enumerate(submodules_in_call_order)}

        for eqn in jaxpr.eqns:
            execution_index = submodule_execution_index_by_name.pop(eqn.primitive.name, None)
            if execution_index is not None:
                permutation.append(execution_index)

        assert len(submodule_execution_index_by_name) == 0
        assert len(permutation) == len(submodules_in_call_order)

        return parametrized.inverse_permutation(permutation)

    @staticmethod
    def _partialize(flat_inputs):
        return map(lambda x: pe.PartialVal((raise_to_shaped(jc.get_aval(x)), jc.unit)), flat_inputs)


class Parameter(parametrized):
    def __init__(self, init_parameter, name=None):
        self._init_parameter = init_parameter
        super().__init__(fun=None, name=name if name else 'parameter')

    def apply(self, parameters, *inputs, jit=False):
        return parameters

    def _init_parameters_dict(self, rng, *example_inputs):
        return self._init_parameter(rng)


class ShapedParametrized:
    """Represents a parametrized module with given example inputs."""

    def __init__(self, parametrized, *example_inputs):
        self.parametrized = parametrized
        self.example_inputs = example_inputs

    def apply_from(self, reuse, jit=False):
        return self.parametrized.apply_from(reuse, *self.example_inputs, jit=jit)

    def init_parameters(self, rng):
        return self.parametrized.init_parameters(rng, *self.example_inputs)


def save(parameters, path: Path):
    with path.open('wb') as file:
        dill.dump(parameters, file)


def load(path: Path):
    with path.open('rb') as file:
        return dill.load(file)
