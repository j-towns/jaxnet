import pytest
from jax import numpy as np, jit, random, lax
from jax.random import PRNGKey

from examples.mnist_vae import gaussian_sample, bernoulli_logpdf, gaussian_kl
from jaxnet import parametrized, Parameter, Dense, Sequential, relu, Conv, Conv1D, \
    ConvTranspose, Conv1DTranspose, flatten, MaxPool, zeros, GRUCell, Rnn, softmax, SumPool, \
    AvgPool, Dropout, BatchNorm, save_params, load_params, softplus, parameter


def random_inputs(input_shape, rng=PRNGKey(0)):
    if type(input_shape) is tuple:
        return random.uniform(rng, input_shape, np.float32)
    elif type(input_shape) is list:
        return [random_inputs(rng, shape) for shape in input_shape]
    else:
        raise TypeError(type(input_shape))


def assert_params_equal(p, p_):
    if isinstance(p, np.ndarray):
        assert np.array_equal(p, p_)
        return

    assert isinstance(p, tuple) or isinstance(p, list) or isinstance(p, dict)
    assert isinstance(p, tuple) == isinstance(p_, tuple)
    assert isinstance(p, list) == isinstance(p_, list)
    assert isinstance(p, dict) == isinstance(p_, dict)

    assert len(p) == len(p_)

    if isinstance(p, dict):
        for k, e in p.items():
            assert_params_equal(e, p_[k])
    else:
        for e, e_ in zip(p, p_):
            assert_params_equal(e, e_)


def assert_dense_params_equal(p, p_):
    assert len(p) == len(p_)
    assert np.array_equal(p.kernel, p_.kernel)
    assert np.array_equal(p.bias, p_.bias)


def test_external_submodule():
    layer = Dense(3)

    @parametrized
    def net_fun(inputs):
        return 2 * layer(inputs)

    inputs = random_inputs((2,))
    params = net_fun.init_params(PRNGKey(0), inputs)
    out = net_fun.apply(params, inputs)
    assert out.shape == (3,)

    out_ = net_fun.apply(params, inputs)
    assert np.array_equal(out, out_)

    out_ = jit(net_fun.apply)(params, inputs)
    assert np.allclose(out, out_)


def test_default_argument_submodule():
    @parametrized
    def net_fun(inputs, layer=Dense(3)):
        return 2 * layer(inputs)

    inputs = random_inputs((2,))
    params = net_fun.init_params(PRNGKey(0), inputs)
    out = net_fun.apply(params, inputs)
    assert out.shape == (3,)

    out_ = net_fun.apply(params, inputs)
    assert np.array_equal(out, out_)

    out_ = jit(net_fun.apply)(params, inputs)
    assert np.allclose(out, out_)


def test_inline_submodule():
    @parametrized
    def net_fun(inputs):
        layer = Dense(3)
        return 2 * layer(inputs)

    inputs = random_inputs((2,))
    params = net_fun.init_params(PRNGKey(0), inputs)
    out = net_fun.apply(params, inputs)
    assert out.shape == (3,)

    out_ = net_fun.apply(params, inputs)
    assert np.array_equal(out, out_)

    out_ = jit(net_fun.apply)(params, inputs)
    assert np.allclose(out, out_)


def test_external_submodule_partial_jit():
    layer = Dense(3)

    @parametrized
    def net_fun(inputs):
        return jit(lambda x: 2 * x)(layer(inputs))

    inputs = random_inputs((2,))
    params = net_fun.init_params(PRNGKey(0), inputs)
    out = net_fun.apply(params, inputs)
    assert out.shape == (3,)


@pytest.mark.skip('TODO')
def test_external_submodule_partial_jit_submodule():
    layer = Dense(3)

    @parametrized
    @jit
    def net_fun(inputs):
        return layer(inputs)

    inputs = random_inputs((2,))
    params = net_fun.init_params(PRNGKey(0), inputs)
    out = net_fun.apply(params, inputs)
    assert out.shape == (3,)


def test_inline_sequential_submodule():
    @parametrized
    def inner(inputs):
        layer = Sequential(Dense(2), relu)
        return layer(inputs)

    @parametrized
    def outer(inputs):
        return inner(inner(inputs))

    inputs = np.zeros((1, 2))
    params = outer.init_params(PRNGKey(0), inputs)
    assert (2,) == params.inner.sequential.dense.bias.shape
    out = outer.apply(params, inputs)
    assert (1, 2) == out.shape


def test_external_submodule2():
    layer = Dense(2, zeros, zeros)

    @parametrized
    def net(inputs):
        return layer(inputs)

    inputs = np.zeros((1, 2))

    params = net.init_params(PRNGKey(0), inputs)
    assert_params_equal(((np.zeros((2, 2)), np.zeros(2)),), params)

    out = net.apply(params, inputs)
    assert np.array_equal(np.zeros((1, 2)), out)

    out_ = jit(net.apply)(params, inputs)
    assert np.array_equal(out, out_)


def test_external_sequential_submodule():
    layer = Sequential(Conv(4, (2, 2)), flatten, relu, Dense(3), relu, Dense(2),
                       Sequential(Dense(2), relu))
    inputs = np.zeros((1, 5, 5, 2))

    params = layer.init_params(PRNGKey(0), inputs)
    assert (4,) == params.conv.bias.shape
    assert (3,) == params.dense0.bias.shape
    assert (3, 2) == params.dense1.kernel.shape
    assert (2,) == params.dense1.bias.shape
    assert (2,) == params.sequential.dense.bias.shape

    out = layer.apply(params, inputs)
    assert (1, 2) == out.shape

    out_ = jit(layer.apply)(params, inputs)
    assert np.allclose(out, out_)


def test_internal_param_sharing():
    @parametrized
    def shared_net(inputs, layer=Dense(2, zeros, zeros)):
        return layer(layer(inputs))

    inputs = np.zeros((1, 2))
    params = shared_net.init_params(PRNGKey(0), inputs)
    assert_params_equal(((np.zeros((2, 2)), np.zeros(2),),), params)

    out = shared_net.apply(params, inputs)
    assert np.array_equal(np.zeros((1, 2)), out)

    out_ = jit(shared_net.apply)(params, inputs)
    assert np.array_equal(out, out_)


def test_internal_param_sharing2():
    @parametrized
    def shared_net(inputs, layer=Sequential(Dense(2, zeros, zeros), relu)):
        inputs = layer(inputs)
        return layer(inputs)

    inputs = np.zeros((1, 2))
    params = shared_net.init_params(PRNGKey(0), inputs)

    assert_params_equal((((np.zeros((2, 2)), np.zeros(2)),),), params)
    out = shared_net.apply(params, inputs)
    assert np.array_equal(np.zeros((1, 2)), out)


def test_no_reuse():
    inputs = np.zeros((1, 2))

    layer = Dense(5)
    net1 = Sequential(layer, Dense(2))
    p1 = net1.init_params(PRNGKey(0), inputs)

    net2 = Sequential(layer, Dense(3))
    p2 = net2.init_params(PRNGKey(1), inputs)

    assert p1[0].kernel.shape == p2[0].kernel.shape
    assert p1[0].bias.shape == p2[0].bias.shape
    assert not np.array_equal(p1[0][0], p2[0][0])
    assert not np.array_equal(p1[0][1], p2[0][1])


def test_external_param_sharing():
    layer = Dense(2, zeros, zeros)
    shared_net = Sequential(layer, layer)

    inputs = np.zeros((1, 2))
    params = shared_net.init_params(PRNGKey(0), inputs)
    assert_params_equal(((np.zeros((2, 2)), np.zeros(2)),), params)

    out = shared_net.apply(params, inputs)
    assert np.array_equal(np.zeros((1, 2)), out)

    out = jit(shared_net.apply)(params, inputs)
    assert np.array_equal(np.zeros((1, 2)), out)


def test_submodule_reuse():
    inputs = np.zeros((1, 2))

    layer = Dense(5)
    net1 = Sequential(layer, Dense(2))
    net2 = Sequential(layer, Dense(3))

    layer_params = layer.init_params(PRNGKey(0), inputs)
    net1_params = net1.init_params(PRNGKey(1), inputs, reuse={layer: layer_params})
    net2_params = net2.init_params(PRNGKey(2), inputs, reuse={layer: layer_params})

    out1 = net1.apply(net1_params, inputs)
    assert out1.shape == (1, 2)

    out2 = net2.apply(net2_params, inputs)
    assert out2.shape == (1, 3)

    assert_dense_params_equal(layer_params, net1_params[0])
    assert_dense_params_equal(layer_params, net2_params[0])


def test_no_params():
    @parametrized
    def double(inputs):
        return 2 * inputs

    inputs = np.zeros((1, 3))
    params = double.init_params(PRNGKey(0), inputs)
    assert_params_equal((), params)

    out = double.apply(params, inputs)
    assert np.array_equal(np.zeros((1, 3)), out)

    out_ = jit(double.apply)(params, inputs)
    assert np.array_equal(out, out_)


def test_Dense():
    net = Dense(2, kernel_init=zeros, bias_init=zeros)
    inputs = np.zeros((1, 3))

    params = net.init_params(PRNGKey(0), inputs)
    assert_params_equal((np.zeros((3, 2)), np.zeros(2)), params)

    out = net.apply(params, inputs)
    assert np.array_equal(np.zeros((1, 2)), out)

    out_ = jit(net.apply)(params, inputs)
    assert np.array_equal(out, out_)

    params_ = net.shaped(inputs).init_params(PRNGKey(0))
    assert_params_equal(params, params_)


def test_param_and_submodule_mixed():
    @parametrized
    def linear_map(inputs):
        kernel = Parameter('kernel', (inputs.shape[-1], 2), zeros, inputs)
        return np.dot(inputs, kernel)

    @parametrized
    def dense(inputs):
        return linear_map(inputs) + Parameter('bias', (2,), zeros, inputs)

    inputs = np.zeros((1, 3))

    params = dense.init_params(PRNGKey(0), inputs)
    assert (2,) == params.bias.shape
    assert (3, 2) == params.linear_map.kernel.shape

    out = dense.apply(params, inputs)
    assert np.array_equal(np.zeros((1, 2)), out)

    out_ = jit(dense.apply)(params, inputs)
    assert np.array_equal(out, out_)


@pytest.mark.skip('TODO')
def test_mixed_up_execution_order():
    @parametrized
    def dense(inputs):
        bias = Parameter('bias', (2,), zeros, inputs)
        return np.dot(inputs, Parameter('kernel', (inputs.shape[-1], 2), zeros, inputs)) + bias

    inputs = np.zeros((1, 3))

    params = dense.init_params(PRNGKey(0), inputs)
    assert (2,) == params.bias.shape
    assert (3, 2) == params.kernel.shape

    out = dense.apply(params, inputs)
    assert np.array_equal(np.zeros((1, 2)), out)

    out_ = jit(dense.apply)(params, inputs)
    assert np.array_equal(out, out_)


def test_submodule_reuse_top_level():
    net = Dense(2)
    inputs = np.zeros((1, 3))
    params = net.init_params(PRNGKey(0), inputs)
    out = net.apply(params, inputs)

    params_ = net.init_params(PRNGKey(1), inputs, reuse={net: params})
    assert_dense_params_equal(params, params_)

    out_ = net.apply(params_, inputs)
    assert np.array_equal(out, out_)


def test_params_from():
    layer = Dense(2)
    net = Sequential(layer, relu)
    inputs = np.zeros((1, 3))
    layer_params = layer.init_params(PRNGKey(0), inputs)

    params_ = net.params_from({layer: layer_params}, inputs)
    assert_params_equal((layer_params,), params_)

    out = net.apply(params_, inputs)

    out_ = net.apply_from({layer: layer_params}, inputs)
    assert np.array_equal(out, out_)

    out_ = net.apply_from({layer: layer_params}, inputs, jit=True)
    assert np.array_equal(out, out_)


def test_params_from_subsubmodule():
    subsublayer = Dense(2)
    sublayer = Sequential(subsublayer, relu)
    net = Sequential(sublayer, np.sum)
    inputs = np.zeros((1, 3))
    params = net.init_params(PRNGKey(0), inputs)
    out = net.apply(params, inputs)

    subsublayer_params = subsublayer.init_params(PRNGKey(0), inputs)

    params_ = net.params_from({subsublayer: subsublayer_params}, inputs)
    assert_dense_params_equal(subsublayer_params, params_[0][0])
    out_ = net.apply(params_, inputs)
    assert out.shape == out_.shape

    out_ = net.apply_from({subsublayer: subsublayer_params}, inputs)
    assert out.shape == out_.shape

    out_ = net.apply_from({subsublayer: subsublayer_params}, inputs, jit=True)
    assert out.shape == out_.shape


def test_params_from_top_level():
    net = Dense(2)
    inputs = np.zeros((1, 3))
    params = net.init_params(PRNGKey(0), inputs)
    out = net.apply(params, inputs)

    params_ = net.params_from({net: params}, inputs)
    assert_dense_params_equal(params, params_)
    out_ = net.apply(params_, inputs)
    assert np.array_equal(out, out_)

    out_ = net.apply_from({net: params}, inputs)
    assert np.array_equal(out, out_)

    out_ = net.apply_from({net: params}, inputs, jit=True)
    assert np.array_equal(out, out_)


def test_params_from_shared_submodules():
    sublayer = Dense(2)
    a = Sequential(sublayer, relu)
    b = Sequential(sublayer, np.sum)

    @parametrized
    def net(inputs):
        return a(inputs) * b(inputs)

    inputs = np.zeros((1, 3))
    a_params = a.init_params(PRNGKey(0), inputs)
    out = a.apply(a_params, inputs)

    params = net.params_from({a: a_params}, inputs)
    assert_params_equal(a_params.dense.kernel, params.sequential0.dense.kernel)
    assert_params_equal(a_params.dense.kernel, params.sequential1.dense.kernel)
    out = net.apply(params, inputs)

    out_ = net.apply_from({a: a_params}, inputs)
    assert np.array_equal(out, out_)

    out_ = net.apply_from({a: a_params}, inputs, jit=True)
    assert np.array_equal(out, out_)

    out_ = net.apply_from({a.shaped(inputs): a_params}, inputs)
    assert np.array_equal(out, out_)

    out_ = net.apply_from({a.shaped(inputs): a_params}, inputs, jit=True)
    assert np.array_equal(out, out_)

    out_ = net.shaped(inputs).apply_from({a: a_params})
    assert np.array_equal(out, out_)

    out_ = net.shaped(inputs).apply_from({a: a_params}, jit=True)
    assert np.array_equal(out, out_)

    out_ = net.shaped(inputs).apply_from({a.shaped(inputs): a_params})
    assert np.array_equal(out, out_)

    out_ = net.shaped(inputs).apply_from({a.shaped(inputs): a_params}, jit=True)
    assert np.array_equal(out, out_)


def test_params_from_shared_submodules2():
    sublayer = Dense(2)
    a = Sequential(sublayer, relu)
    b = Sequential(sublayer, np.sum)

    @parametrized
    def net(inputs):
        return a(inputs), b(inputs)

    inputs = np.zeros((1, 3))
    a_params = a.init_params(PRNGKey(0), inputs)
    out = a.apply(a_params, inputs)

    params = net.params_from({a: a_params}, inputs)
    assert_dense_params_equal(a_params.dense, params.sequential0.dense)
    assert_dense_params_equal(a_params.dense, params.sequential1.dense)
    # TODO parameters are duplicated, optimization with weight sharing is wrong:
    # TODO instead: assert 1 == len(params)
    out_, _ = net.apply(params, inputs)
    assert np.array_equal(out, out_)


def test_example():
    net = Sequential(Conv(2, (3, 3)), relu, flatten, Dense(4), softmax)
    batch = np.zeros((3, 5, 5, 1))
    params = net.init_params(PRNGKey(0), batch)
    assert (2,) == params.conv.bias.shape
    assert (4,) == params.dense.bias.shape

    out = net.apply(params, batch)
    assert (3, 4) == out.shape

    out_ = jit(net.apply)(params, batch)
    assert out.shape == out_.shape


@pytest.mark.parametrize('channels', [2, 3])
@pytest.mark.parametrize('filter_shape', [(1, 1), (2, 3)])
@pytest.mark.parametrize('padding', ["SAME", "VALID"])
@pytest.mark.parametrize('strides', [None, (2, 1)])
@pytest.mark.parametrize('input_shape', [(2, 10, 11, 1)])
@pytest.mark.parametrize('dilation', [None, (1, 2)])
def test_Conv_runs(channels, filter_shape, padding, strides, input_shape, dilation):
    conv = Conv(channels, filter_shape, strides=strides, padding=padding, dilation=dilation)
    inputs = random_inputs(input_shape)
    params = conv.init_params(PRNGKey(0), inputs)
    conv.apply(params, inputs)


@pytest.mark.parametrize('channels', [2, 3])
@pytest.mark.parametrize('filter_shape', [(1,), (2,), (3,)])
@pytest.mark.parametrize('padding', ["SAME", "VALID"])
@pytest.mark.parametrize('strides', [None, (1,), (2,)])
@pytest.mark.parametrize('input_shape', [(2, 10, 1)])
def test_Conv1DTranspose_runs(channels, filter_shape, padding, strides, input_shape):
    conv = Conv1D(channels, filter_shape, strides=strides, padding=padding)
    inputs = random_inputs(input_shape)
    params = conv.init_params(PRNGKey(0), inputs)
    conv.apply(params, inputs)


@pytest.mark.parametrize('channels', [2, 3])
@pytest.mark.parametrize('filter_shape', [(1, 1), (2, 3), (3, 3)])
@pytest.mark.parametrize('padding', ["SAME", "VALID"])
@pytest.mark.parametrize('strides', [None, (2, 1), (2, 2)])
@pytest.mark.parametrize('input_shape', [(2, 10, 11, 1)])
def test_ConvTranspose_runs(channels, filter_shape, padding, strides, input_shape):
    convt = ConvTranspose(channels, filter_shape, strides=strides, padding=padding)
    inputs = random_inputs(input_shape)
    params = convt.init_params(PRNGKey(0), inputs)
    convt.apply(params, inputs)


@pytest.mark.parametrize('channels', [2, 3])
@pytest.mark.parametrize('filter_shape', [(1,), (2,), (3,)])
@pytest.mark.parametrize('padding', ["SAME", "VALID"])
@pytest.mark.parametrize('strides', [None, (1,), (2,)])
@pytest.mark.parametrize('input_shape', [(2, 10, 1)])
def test_Conv1DTranspose_runs(channels, filter_shape, padding, strides, input_shape):
    convt = Conv1DTranspose(channels, filter_shape, strides=strides, padding=padding)
    inputs = random_inputs(input_shape)
    params = convt.init_params(PRNGKey(0), inputs)
    convt.apply(params, inputs)


def test_Conv_flatten_shape():
    conv = Conv(2, filter_shape=(3, 3), padding='SAME', kernel_init=zeros, bias_init=zeros)
    inputs = np.zeros((1, 5, 5, 2))

    params = conv.init_params(PRNGKey(0), inputs)
    out = conv.apply(params, inputs)
    assert np.array_equal(np.zeros((1, 5, 5, 2)), out)

    flattened = Sequential(conv, flatten)
    out = flattened.apply_from({conv: params}, inputs)
    assert np.array_equal(np.zeros((1, 50)), out)


@pytest.mark.parametrize('Pool', (MaxPool, SumPool, AvgPool))
def test_Conv_pool_shape(Pool):
    conv = Conv(2, filter_shape=(3, 3), padding='SAME', kernel_init=zeros, bias_init=zeros)
    inputs = np.zeros((1, 5, 5, 2))

    pooled = Sequential(conv, Pool(window_shape=(1, 1), strides=(2, 2)))
    params = pooled.init_params(PRNGKey(0), inputs)
    out = pooled.apply(params, inputs)
    assert np.array_equal(np.zeros((1, 3, 3, 2)), out)


@pytest.mark.parametrize('mode', ('train', 'test'))
def test_Dropout_shape(mode, input_shape=(1, 2, 3)):
    dropout = Dropout(.9, mode=mode)
    inputs = np.zeros(input_shape)
    out = dropout(inputs, PRNGKey(0))
    assert np.array_equal(np.zeros(input_shape), out)

    out_ = dropout(inputs, rng=PRNGKey(0))
    assert np.array_equal(out, out_)

    try:
        dropout(inputs)
        assert False
    except ValueError as e:
        assert 'dropout requires to be called with a PRNG key argument. ' \
               'That is, instead of `dropout(params, inputs)`, ' \
               'call it like `dropout(inputs, key)` ' \
               'where `key` is a jax.random.PRNGKey value.' == str(e)


def test_GRUCell_shape():
    gru_cell, init_carry = GRUCell(10, zeros)

    x = np.zeros((2, 3))
    carry = init_carry(batch_size=2)
    params = gru_cell.init_params(PRNGKey(0), carry, x)
    out = gru_cell.apply(params, carry, x)
    # TODO remove ".xs":
    out = out.xs

    assert (2, 10) == out[0].shape
    assert (2, 10) == out[1].shape


def test_Rnn_shape():
    inputs = np.zeros((2, 5, 4))
    rnn = Rnn(*GRUCell(3, zeros))
    params = rnn.init_params(PRNGKey(0), inputs)

    assert len(params) == 1
    assert len(params.gru_cell) == 3
    assert np.array_equal(np.zeros((7, 3)), params.gru_cell.update_kernel)
    assert np.array_equal(np.zeros((7, 3)), params.gru_cell.reset_kernel)
    assert np.array_equal(np.zeros((7, 3)), params.gru_cell.compute_kernel)

    out = rnn.apply(params, inputs)
    assert np.array_equal(np.zeros((2, 5, 3)), out)


def test_Rnn_net_shape():
    length = 5
    carry_size = 3
    class_count = 4
    inputs = np.zeros((1, length, 4))

    def rnn(): return Rnn(*GRUCell(carry_size, zeros))

    net = Sequential(
        rnn(),
        rnn(),
        rnn(),
        lambda x: np.reshape(x, (-1, carry_size)),  # -> same weights for all time steps
        Dense(class_count, zeros, zeros),
        softmax,
        lambda x: np.reshape(x, (-1, length, class_count)))

    params = net.init_params(PRNGKey(0), inputs)

    assert len(params) == 4
    cell = params.rnn0.gru_cell
    assert len(cell) == 3
    assert np.array_equal(np.zeros((7, 3)), cell.update_kernel)
    assert np.array_equal(np.zeros((7, 3)), cell.reset_kernel)
    assert np.array_equal(np.zeros((7, 3)), cell.compute_kernel)

    out = net.apply(params, inputs)
    assert np.array_equal(.25 * np.ones((1, 5, 4)), out)


def test_scan_unparametrized_cell():
    def cell(carry, x):
        return np.array([2]) * carry * x, np.array([2]) * carry * x

    @parametrized
    def rnn(inputs):
        _, outs = lax.scan(cell, np.zeros((2,)), inputs)
        return outs

    inputs = np.zeros((3,))

    params = rnn.init_params(PRNGKey(0), inputs)
    outs = rnn.apply(params, inputs)

    assert (3, 2) == outs.shape


def test_scan_parametrized_cell_without_params():
    @parametrized
    def cell(carry, x):
        return np.array([2]) * carry * x, np.array([2]) * carry * x

    @parametrized
    def rnn(inputs):
        _, outs = lax.scan(cell, np.zeros((2,)), inputs)
        return outs

    inputs = np.zeros((3,))

    params = rnn.init_params(PRNGKey(0), inputs)
    outs = rnn.apply(params, inputs)

    assert (3, 2) == outs.shape


def test_scan_parametrized_cell():
    @parametrized
    def cell(carry, x):
        scale = Parameter('s', (2,), zeros, carry)
        return scale * np.array([2]) * carry * x, scale * np.array([2]) * carry * x

    @parametrized
    def rnn(inputs):
        _, outs = lax.scan(cell, np.zeros((2,)), inputs)
        return outs

    inputs = np.zeros((3,))

    params = rnn.init_params(PRNGKey(0), inputs)
    outs = rnn.apply(params, inputs)

    assert (3, 2) == outs.shape


@pytest.mark.parametrize('center', (False, True))
@pytest.mark.parametrize('scale', (False, True))
def test_BatchNorm_shape_NHWC(center, scale):
    input_shape = (4, 5, 6, 7)
    batch_norm = BatchNorm(axis=(0, 1, 2), center=center, scale=scale)
    inputs = random_inputs(input_shape)

    params = batch_norm.init_params(PRNGKey(0), inputs)
    out = batch_norm.apply(params, inputs)

    assert out.shape == input_shape
    if center:
        assert params.beta.shape == (7,)
    if scale:
        assert params.gamma.shape == (7,)


@pytest.mark.parametrize('center', (False, True))
@pytest.mark.parametrize('scale', (False, True))
def test_BatchNorm_shape_NCHW(center, scale):
    input_shape = (4, 5, 6, 7)
    batch_norm = BatchNorm(axis=(0, 2, 3), center=center, scale=scale)

    inputs = random_inputs(input_shape)
    params = batch_norm.init_params(PRNGKey(0), inputs)
    out = batch_norm.apply(params, inputs)

    assert out.shape == input_shape
    if center:
        assert params.beta.shape == (5,)
    if scale:
        assert params.gamma.shape == (5,)


def test_reuse_example():
    inputs = np.zeros((1, 2))
    net = Dense(5)
    net_params = net.init_params(PRNGKey(0), inputs)

    # train net params...

    transfer_net = Sequential(net, relu, Dense(2))
    transfer_net_params = transfer_net.init_params(PRNGKey(1), inputs, reuse={net: net_params})

    assert transfer_net_params[0] is net_params

    # train transfer_net_params...


def test_InputDependent():
    @parametrized
    def net(inputs):
        return Dense(inputs.shape[0])(inputs)

    inputs = np.zeros((5, 3))
    params = net.init_params(PRNGKey(0), inputs)

    out = net.apply(params, inputs)

    assert (5, 5) == out.shape
    assert str(net).startswith('net')


def test_InputDependent_nested():
    @parametrized
    def layer(inputs):
        return Dense(inputs.shape[0])(inputs)

    net = Sequential(Dense(3), layer)

    inputs = np.zeros((5, 3))
    params = net.init_params(PRNGKey(0), inputs)

    out = net.apply(params, inputs)
    assert (5, 5) == out.shape


def test_sequential_graceful_update_message():
    message = 'Call like Sequential(Dense(10), relu), without "[" and "]". ' \
              '(Or pass iterables with Sequential(*layers).)'
    try:
        Sequential([Dense(2), relu])
        assert False
    except ValueError as e:
        assert message == str(e)

    try:
        Sequential(Dense(2) for _ in range(3))
        assert False
    except ValueError as e:
        assert message == str(e)


def test_save_and_load_params():
    from pathlib import Path

    path = Path('/') / 'tmp' / 'net.params4'

    inputs = np.zeros((1, 2))
    net = Dense(5)
    params = net.init_params(PRNGKey(0), inputs)
    save_params(params, path)
    params_ = load_params(path)

    assert_dense_params_equal(params, params_)


@pytest.mark.skip('TODO')
def test_submodule_without_inputs():
    @parametrized
    def scalar():
        return Parameter('scalar', (), zeros, 0)

    params = scalar.init_params(PRNGKey(0))
    assert_params_equal((), params)

    out = scalar.apply(params)
    assert np.array_equal(np.zeros(()), out)

    out_ = jit(scalar.apply)(params)
    assert np.array_equal(out, out_)


def test_nested_module_without_inputs():
    dense = Dense(2)
    inputs = np.zeros((1, 3))
    params = dense.init_params(PRNGKey(0), inputs)
    assert (3, 2) == params.kernel.shape
    assert (2,) == params.bias.shape
    assert str(dense).startswith('dense')

    out = dense.apply(params, inputs)
    assert (1, 2) == out.shape

    out_ = jit(dense.apply)(params, inputs)
    assert np.allclose(out, out_)


def test_mnist_vae_example():
    @parametrized
    def encode(input):
        input = Sequential(Dense(5), relu, Dense(5), relu)(input)
        mean = Dense(10)(input)
        variance = Sequential(Dense(10), softplus)(input)
        return mean, variance

    decode = Sequential(Dense(5), relu, Dense(5), relu, Dense(5 * 5))

    @parametrized
    def elbo(rng, images):
        mu_z, sigmasq_z = encode(images)
        logits_x = decode(gaussian_sample(rng, mu_z, sigmasq_z))
        return bernoulli_logpdf(logits_x, images) - gaussian_kl(mu_z, sigmasq_z)

    params = elbo.init_params(PRNGKey(0), PRNGKey(0), np.zeros((32, 5 * 5)))
    assert (5, 10) == params.encode.sequential1.dense.kernel.shape


@pytest.mark.skip('TODO')
def test_tuple_input():
    @parametrized
    def net(input_dict):
        return input_dict[0] * input_dict[1] * Parameter('s', (), zeros, input_dict[0])

    inputs = (np.zeros((2,)), np.zeros((2,)))
    params = net.init_params(PRNGKey(0), inputs)
    out = net.apply(params, inputs)
    assert np.array_equal(np.zeros((2, 10)), out)


@pytest.mark.skip('TODO')
def test_dict_input():
    @parametrized
    def net(input_dict):
        return input_dict['a'] * input_dict['b'] * Parameter('s', (), zeros, input_dict['a'])

    inputs = {'a': np.zeros((2,)), 'b': np.zeros((2,))}
    params = net.init_params(PRNGKey(0), inputs)
    out = net.apply(params, inputs)
    assert np.array_equal(np.zeros((2, 10)), out)


def test_tuple_output():
    a = Sequential(Dense(2), relu)

    @parametrized
    def net(inputs):
        return a(inputs), a(inputs)

    inputs = np.zeros((1, 3))
    params = net.init_params(PRNGKey(0), inputs)
    out = net.apply(params, inputs)

    # TODO remove ".xs":
    out1, out2 = out.xs

    assert (1, 2) == out1.shape
    assert np.array_equal(out1, out2)


def test_parameter():
    scalar = parameter('scalar', lambda _: np.zeros(()))
    param = scalar.init_params(PRNGKey(0))

    assert np.zeros(()) == param
    out = scalar.apply(param)
    assert param == out


def test_parameter_simple_class():
    class parameter:
        def __init__(self, name, init_param):
            self.name = name
            self._init_param = init_param

        def apply(self, params, *inputs): return params

        def init_params(self, rng, *example_inputs):
            rng, rng_param = random.split(rng)
            return self._init_param(rng_param)

    scalar = parameter('scalar', lambda _: np.zeros(()))
    param = scalar.init_params(PRNGKey(0))

    assert np.zeros(()) == param
    out = scalar.apply(param)
    assert param == out
