# Why functional API?

Side effects / mutable state come at a cost.
Machine learning is no exception.

### Functional code allows new ways of optimization.
JAX allows funcitonal `numpy` code to be accelerated with `jit` and run on GPU. 
Here are two use cases:
- In JAXnet, weights are explicitely initialized into an object controlled by the user. 
Optimization returns a new version of weights instead of mutating them inline.
This allows whole training loops to be compiled / run on GPU ([demo](examples/mnist_classifier.py#75)).
- If you use functional `numpy/scipy` for pre-/postprocessing, replacing `numpy` with `jax.numpy` in your import allows you to compile it / run it on GPU. 
([demo](examples/mnist_classifier.py#79)).

### Reusing code relying on a global compute graph can be a hassle.
This is particularly true for more advanced use cases, say:
You want to use existing TensorFlow code that manipulates variables by using their global name. 
You need to instantiate this network with two different sets of weights, and combine their output.
Since you want your code to be fast, you'd like to run both on GPU and code on GPU.
While solutions exists, code like this is brittle and hard to maintain.

JAXnet has no global compute graph.
All network definitions and weights are contained in (read-only) objects.
This encourages code that is easy to reuse.

### Global random state is inflexible.
Example: While trained a VAE, you might want to see how reconstructions for a fixed latent variable sample improves over time.
In popular frameworks, you would have resupply get a bunch of latent variable samples and resupply them to the network, requiring some extra code.


In JAXnet you can simply fix the sampling random seed for this specific part of the network. ([demo](examples/mnist_vae.py#L91))

## What about existing frameworks?

Here is a crude comparison of some existing libraries:

| Deep Learning Library                 | [Tensorflow2/Keras](https://www.tensorflow.org/beta) | [PyTorch](https://pytorch.org)  | [JAXnet](https://github.com/JuliusKunze/jaxnet) |
|-------------------------|-------------------|----------|--------|
| Immutable weights       | ❌                | ❌      | ✅     |
| No global compute graph | ❌                | ✅      | ✅     |
| No global random key    | ❌                | ❌      | ✅     |

JAXnet is independent of [stax](https://github.com/google/jax/blob/master/jax/experimental/stax.py).
The main motivation over stax is to simplify nesting modules.
Find details and porting instructions [here](STAX.md).