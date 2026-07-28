This project was born with a few motivations and constraints in mind, in relation to the existing implementations:

- Improve maintenance and addition of new features

  The primary purpose was to provide a cleaner code while implementing the same features.
  This involves making more use of standard dependencies (e.g. pydantic) instead of manually implementing features.
  This is particularly true for the annotations system, taking care of validating plan addition on the queue.

  Another change in that direction involves joining the three related projects (`bluesky-queueserver`, `bluesky-httpserver` and `bluesky-queueserver-api`)
  into a single repository and project, facilitating adding features by reducing duplicate work, and easing implantation and operation by
  providing a single service that can already provide the API needed by clients.

- Maintain backwards compatibility when it makes sense (i.e. make it a plug-and-play replacement for the majority of current usage patterns)

  In order to ease implantation in production environments and reduce development costs of such, a big constraint in the implementation
  of this project was to provide APIs and configuration compatible with the current implementation. This would allow for some really nice amenities:

  1. Switching from `bluesky-queueserver` and friends to `satellite` would be as painless as possible. Simply switching the installed packages and
  the entrypoint names should suffice for the entire service to work as intended.

  2. Tests targetting `bluesky-queueserver` and friends could be reused with `satellite`. This would allow us to reuse existing work done
  by previous people into ensuring the implementation works as expected. It also ensures that the project is really backwards compatible, making it
  easier for new people to pick up the project.

  3. By making everything have more or less the same inputs and outputs as the current implementations, it makes it easier to contribute chunks of
  functionality from `satellite` back into the existing projects, in case this new rewrite does not resonate with people, and the community decides
  on continuing with the existing projects. While this does mean architectural changes are harder to transpose, smaller changes can still be reused.

- Improve the implementation of HTTP endpoints, following verb best practices, ensuring it works fine with other tools, like VPNs.

  One major motivator for overhaling the current implementation has to do with how it handles HTTP requests. In particular, the way it handles
  GET requests, with a payload associated with the requests, is not recommended by official protocol specifications, and is blocked by certain
  VPN providers, since it can pose some security risks.

- Improve dependency tree, especially for CLI and Python clients

  In the current implementations, `bluesky-queueserver` depends directly on packages like `jupyter-client`, `bluesky` and `pyzmq`,
  and `bluesky-queueserver-api` depends directly on that. This means that all clients using the provided API package need to carry a long list
  of unused dependencies, at least for their purposes.

  One motivation for this package is to also modularize them more, allowing clients to carry a slimmer set of dependencies when possible.

- Modify architecture to support desired features (e.g. multiple queues)

  A big motivation for rewriting the project instead of trying to slowly change the existing implementations is because of some architectural
  decisions that really get on the way of developing some new features.

  For instance, one common problem we encounter in some beamlines is the issue of concurrency: there are two experimental setups
  (or an experimental setup and a sample preparation environment) that can be controlled almost entirely independent of one another.
  In order to optimize beam time, we want to run Bluesky plans on both of them at the same time (given some constraints, like not touching
  the same devices at the same time), possibly enqueuing different plans on both of them.

  This would then entail in some way having two queues at once. With the current implementations, most alternatives to this have some downside in
  one way or another: having two queueservers means having two httpserver too, creating a lot of new services whose health need to be monitored,
  each of which consumes some overhead amount of resources, and it doesn't provide any clean way to do cross-queue validations (like preventing
  two plans from running at the same time while they try to use the same devices).

  One aim of this project is to support such use-cases as first-class citizens, by using a single service to handle them all, with internal
  management of API route prefixes for accessing the different queues, and a mechanism for customizing behavior when conflicts arise.
