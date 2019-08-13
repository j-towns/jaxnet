import pytest
from jax import numpy as np, jit, random
from jax.random import PRNGKey

from jaxnet import parametrized, Dense, Sequential, relu, Conv, flatten, MaxPool, zeros, GRUCell, \
    Rnn, softmax, SumPool, AvgPool, Dropout, BatchNorm


def random_inputs(input_shape, rng=PRNGKey(0)):
    if type(input_shape) is tuple:
        return random.uniform(rng, input_shape, np.float32)
    elif type(input_shape) is list:
        return [random_inputs(rng, shape) for shape in input_shape]
    else:
        raise TypeError(type(input_shape))


def test_params():
    net = Dense(2, kernel_init=zeros, bias_init=zeros)
    inputs = np.zeros((1, 3))

    params = net.init_params(PRNGKey(0), inputs)
    assert len(params) == 2
    assert np.array_equal(np.zeros((3, 2)), params.kernel)
    assert np.array_equal(np.zeros(2), params.bias)

    out = net(params, inputs)
    assert np.array_equal(np.zeros((1, 2)), out)

    out_ = jit(net)(params, inputs)
    assert np.array_equal(out, out_)


def test_submodule():
    @parametrized
    def net(inputs, layer=Dense(2, zeros, zeros)):
        return layer(inputs)

    inputs = np.zeros((1, 2))

    params = net.init_params(PRNGKey(0), inputs)
    assert len(params) == 1
    assert len(params.layer) == 2
    assert np.array_equal(np.zeros((2, 2)), params.layer.kernel)
    assert np.array_equal(np.zeros(2), params.layer.bias)

    out = net(params, inputs)
    assert np.array_equal(np.zeros((1, 2)), out)

    out_ = jit(net)(params, inputs)
    assert np.array_equal(out, out_)


def test_submodule_list():
    layer = Sequential([Dense(2, zeros, zeros), relu])
    inputs = np.zeros((1, 2))

    params = layer.init_params(PRNGKey(0), inputs)
    assert len(params) == 1
    assert len(params.layers) == 2
    assert np.array_equal(np.zeros((2, 2)), params.layers[0].kernel)
    assert np.array_equal(np.zeros(2), params.layers[0].bias)
    assert params.layers[1] == ()

    out = layer(params, inputs)
    assert np.array_equal(np.zeros((1, 2)), out)

    out_ = jit(layer)(params, inputs)
    assert np.array_equal(out, out_)


def assert_dense_params_equal(p, p_):
    assert len(p) == len(p_)
    assert np.array_equal(p.kernel, p_.kernel)
    assert np.array_equal(p.bias, p_.bias)


def test_internal_param_sharing():
    @parametrized
    def shared_net(inputs, layer=Dense(2, zeros, zeros)):
        return layer(layer(inputs))

    inputs = np.zeros((1, 2))
    params = shared_net.init_params(PRNGKey(0), inputs)
    assert len(params) == 1
    assert len(params.layer) == 2
    assert np.array_equal(np.zeros((2, 2)), params.layer.kernel)
    assert np.array_equal(np.zeros(2), params.layer.bias)

    out = shared_net(params, inputs)
    assert np.array_equal(np.zeros((1, 2)), out)

    out_ = jit(shared_net)(params, inputs)
    assert np.array_equal(out, out_)


def test_internal_param_sharing2():
    @parametrized
    def shared_net(inputs, layer=Sequential([Dense(2, zeros, zeros), relu])):
        inputs = layer(inputs)
        return layer(inputs)

    inputs = np.zeros((1, 2))
    params = shared_net.init_params(PRNGKey(0), inputs)

    assert len(params) == 1
    assert len(params.layer) == 1
    assert len(params.layer.layers) == 2
    assert len(params.layer.layers[0]) == 2
    assert np.array_equal(np.zeros((2, 2)), params.layer.layers[0].kernel)
    assert np.array_equal(np.zeros(2), params.layer.layers[0].bias)

    out = shared_net(params, inputs)
    assert np.array_equal(np.zeros((1, 2)), out)


def test_multiple_init_params_calls():
    inputs = np.zeros((1, 2))

    layer = Dense(5)
    net1 = Sequential([layer, Dense(2)])
    p1 = net1.init_params(PRNGKey(0), inputs)

    net2 = Sequential([layer, Dense(3)])
    p2 = net2.init_params(PRNGKey(1), inputs)

    assert p1.layers[0].kernel.shape == p2.layers[0].kernel.shape
    assert not np.array_equal(p1.layers[0].kernel, p2.layers[0].kernel)


@pytest.mark.skip(reason="TODO reconsider design")
def test_external_param_sharing():
    layer = Dense(2, zeros, zeros)
    shared_net = Sequential([layer, layer])

    inputs = np.zeros((1, 2))
    params = shared_net.init_params(PRNGKey(0), inputs)
    assert len(params) == 1
    assert len(params.layers) == 2
    assert np.array_equal(np.zeros((2, 2)), params.layers[0].kernel)
    assert np.array_equal(np.zeros(2), params.layers[0].bias)
    assert params.layers[1] == ()

    out = shared_net(params, inputs)
    assert np.array_equal(np.zeros((1, 2)), out)

    out = jit(shared_net)(params, inputs)
    assert np.array_equal(np.zeros((1, 2)), out)


def test_init_params_submodule_reuse():
    inputs = np.zeros((1, 2))

    layer = Dense(5)
    net1 = Sequential([layer, Dense(2)])
    net2 = Sequential([layer, Dense(3)])

    layer_params = layer.init_params(PRNGKey(0), inputs)
    net1_params = net1.init_params(PRNGKey(1), inputs, reuse={layer: layer_params})
    net2_params = net2.init_params(PRNGKey(2), inputs, reuse={layer: layer_params})
    assert_dense_params_equal(layer_params, net1_params.layers[0])
    assert_dense_params_equal(layer_params, net2_params.layers[0])

    out1 = net1(net1_params, inputs)
    assert out1.shape == (1, 2)

    out2 = net2(net2_params, inputs)
    assert out2.shape == (1, 3)


def test_init_params_submodule_reuse_top_level():
    net = Dense(2)
    inputs = np.zeros((1, 3))
    params = net.init_params(PRNGKey(0), inputs)
    out = net(params, inputs)

    params_ = net.init_params(PRNGKey(0), inputs, reuse={net: params})
    assert_dense_params_equal(params, params_)

    out_ = net(params_, inputs)
    assert np.array_equal(out, out_)


def test_join_params():
    layer = Dense(2)
    net = Sequential([layer, relu])
    inputs = np.zeros((1, 3))
    layer_params = layer.init_params(PRNGKey(0), inputs)

    params = net.Parameters((layer_params, ()))
    out = net(params, inputs)

    params_ = net.join_params({layer: layer_params})
    assert len(params_) == 1
    assert_dense_params_equal(layer_params, params_.layers[0])
    assert params_.layers[1] == ()

    out_ = net(params_, inputs)
    assert np.array_equal(out, out_)

    out_ = net.apply_joined({layer: layer_params}, inputs)
    assert np.array_equal(out, out_)

    out_ = net.apply_joined({layer: layer_params}, inputs, jit=True)
    assert np.array_equal(out, out_)


def test_join_params_subsubmodule():
    subsublayer = Dense(2)
    sublayer = Sequential([subsublayer, relu])
    net = Sequential([sublayer, np.sum])
    inputs = np.zeros((1, 3))
    params = net.init_params(PRNGKey(0), inputs)
    out = net(params, inputs)

    subsublayer_params = subsublayer.init_params(PRNGKey(0), inputs)

    params_ = net.join_params({subsublayer: subsublayer_params})
    assert_dense_params_equal(subsublayer_params, params_.layers[0].layers[0])
    out_ = net(params_, inputs)
    assert out.shape == out_.shape

    out_ = net.apply_joined({subsublayer: subsublayer_params}, inputs)
    assert out.shape == out_.shape

    out_ = net.apply_joined({subsublayer: subsublayer_params}, inputs, jit=True)
    assert out.shape == out_.shape


def test_join_params_top_level():
    net = Dense(2)
    inputs = np.zeros((1, 3))
    params = net.init_params(PRNGKey(0), inputs)
    out = net(params, inputs)

    params_ = net.join_params({net: params})
    assert_dense_params_equal(params, params_)
    out_ = net(params_, inputs)
    assert np.array_equal(out, out_)

    out_ = net.apply_joined({net: params}, inputs)
    assert np.array_equal(out, out_)

    out_ = net.apply_joined({net: params}, inputs, jit=True)
    assert np.array_equal(out, out_)


def assert_params_equal(p, p_):
    if isinstance(p, np.ndarray):
        assert np.array_equal(p, p_)
        return

    assert type(p) == type(p_)
    assert len(p) == len(p_)
    for e, e_ in zip(p, p_):
        assert_params_equal(e, e_)


def test_join_params_shared_submodules():
    sublayer = Dense(2)
    part1 = Sequential([sublayer, relu])
    part2 = Sequential([sublayer, np.sum])

    @parametrized
    def net(inputs, part1=part1, part2=part2):
        return part1(inputs), part2(inputs)

    inputs = np.zeros((1, 3))
    net1_params = part1.init_params(PRNGKey(0), inputs)
    out = part1(net1_params, inputs)

    params = net.join_params({part1: net1_params})
    assert_params_equal(net1_params.layers[0], params.part2.layers[0])
    out_ = net(params, inputs)
    assert out.shape == out_[0].shape

    out_ = net.apply_joined({part1: net1_params}, inputs)
    assert out.shape == out_[0].shape

    out_ = net.apply_joined({part1: net1_params}, inputs, jit=True)
    assert out.shape == out_[0].shape


def test_example():
    net = Sequential([Conv(2, (3, 3)), relu, flatten, Dense(4), softmax])
    batch = np.zeros((3, 5, 5, 1))
    params = net.init_params(PRNGKey(0), batch)
    print(params.layers[3].bias)

    out = net(params, batch)
    out_ = jit(net)(params, batch)

    assert (3, 4) == out.shape
    assert out.shape == out_.shape


def test_conv_flatten_shape():
    conv = Conv(2, filter_shape=(3, 3), padding='SAME', kernel_init=zeros, bias_init=zeros)
    inputs = np.zeros((1, 5, 5, 2))

    params = conv.init_params(PRNGKey(0), inputs)
    out = conv(params, inputs)
    assert np.array_equal(np.zeros((1, 5, 5, 2)), out)

    flattened = Sequential([conv, flatten])
    out = flattened({'layers': [params, ()]}, inputs)
    assert np.array_equal(np.zeros((1, 50)), out)


@pytest.mark.parametrize('Pool', (MaxPool, SumPool, AvgPool))
def test_conv_pool_shape(Pool):
    conv = Conv(2, filter_shape=(3, 3), padding='SAME', kernel_init=zeros, bias_init=zeros)
    inputs = np.zeros((1, 5, 5, 2))

    pooled = Sequential([conv, Pool(window_shape=(1, 1), strides=(2, 2))])
    params = pooled.init_params(PRNGKey(0), inputs)
    out = pooled(params, inputs)
    assert np.array_equal(np.zeros((1, 3, 3, 2)), out)


@pytest.mark.parametrize('mode', ('train', 'test'))
def test_dropout_shape(mode, input_shape=(1, 2, 3)):
    dropout = Dropout(.9, mode=mode)
    inputs = np.zeros(input_shape)
    out = dropout(inputs, PRNGKey(0))
    assert np.array_equal(np.zeros(input_shape), out)

    out_ = dropout(inputs, rng=PRNGKey(0))
    assert np.array_equal(out, out_)

    try:
        dropout(inputs)
        assert False
    except ValueError:
        pass
    except:
        assert False


def test_gru_cell_shape():
    gru_cell, init_carry = GRUCell(10, zeros)

    x = np.zeros((2, 3))
    carry = init_carry(batch_size=2)
    params = gru_cell.init_params(PRNGKey(0), carry, x)
    out = gru_cell(params, carry, x)

    assert isinstance(out, tuple)
    assert len(out) == 2

    assert np.array_equal(np.zeros((2, 10)), out[0])
    assert np.array_equal(np.zeros((2, 10)), out[1])


def test_rnn_shape():
    xs = np.zeros((2, 5, 4))
    rnn = Rnn(*GRUCell(3, zeros))
    params = rnn.init_params(PRNGKey(0), xs)

    assert len(params) == 1
    assert len(params.cell) == 3
    assert np.array_equal(np.zeros((7, 3)), params.cell.update_params)
    assert np.array_equal(np.zeros((7, 3)), params.cell.reset_params)
    assert np.array_equal(np.zeros((7, 3)), params.cell.compute_params)

    out = rnn(params, xs)
    assert np.array_equal(np.zeros((2, 5, 3)), out)


def test_rnn_net_shape():
    length = 5
    carry_size = 3
    class_count = 4
    xs = np.zeros((1, length, 4))

    def rnn(): return Rnn(*GRUCell(carry_size, zeros))

    net = Sequential([
        rnn(),
        rnn(),
        rnn(),
        lambda x: np.reshape(x, (-1, carry_size)),  # -> same weights for all time steps
        Dense(class_count, zeros, zeros),
        softmax,
        lambda x: np.reshape(x, (-1, length, class_count))])

    params = net.init_params(PRNGKey(0), xs)

    assert len(params) == 1
    assert len(params.layers[0]) == 1
    cell = params.layers[0].cell
    assert len(cell) == 3
    assert np.array_equal(np.zeros((7, 3)), cell.update_params)
    assert np.array_equal(np.zeros((7, 3)), cell.reset_params)
    assert np.array_equal(np.zeros((7, 3)), cell.compute_params)

    out = net(params, xs)
    assert np.array_equal(.25 * np.ones((1, 5, 4)), out)


@pytest.mark.parametrize('center', (False, True))
@pytest.mark.parametrize('scale', (False, True))
def test_BatchNorm_shape_NHWC(center, scale):
    input_shape = (4, 5, 6, 7)
    batch_norm = BatchNorm(axis=(0, 1, 2), center=center, scale=scale)
    inputs = random_inputs(input_shape)

    params = batch_norm.init_params(PRNGKey(0), inputs)
    out = batch_norm(params, inputs)

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
    out = batch_norm(params, inputs)

    assert out.shape == input_shape
    if center:
        assert params.beta.shape == (5,)
    if scale:
        assert params.gamma.shape == (5,)
