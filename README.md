# Satellite

Satellite is a server intended for running [Bluesky](https://blueskyproject.io/) plans on remote infrastructure, controlled by an HTTP API.

It builds upon the features of [bluesky-queueserver](https://github.com/bluesky/bluesky-queueserver),
[bluesky-httpserver](https://github.com/bluesky/bluesky-httpserver), and [bluesky-queueserver-api](https://github.com/bluesky/bluesky-queueserver-api),
striving to be compatible with it wherever it makes sense to.

## Missing features at the moment

These are the missing features in relation to the original implementations, that are currently targeted for development in the near future:

- [ ] Authentication / Authorization
- [ ] Support for QueueServer's annotation decorator
- [X] Load queue / history state at startup
- [ ] Python library / Command-line interface
  - [X] Python library (Sync and Async versions)
  - [ ] Command-line utility
- [ ] Lock key support
- [ ] ZMQ endpoints
- [ ] HTTP API configuration
- [ ] WebSocket endpoints
- [X] Console API
- [ ] Task support
- [X] Instructions support
- [ ] Loop mode
- [ ] Batch endpoints
- [ ] Queue autostart
- [ ] Permanent metadata

The following are features not present in the current implementation, and that for now are not being considered for development:

- IPython session

Running the Bluesky plans inside an IPython session would involve making it a dependency of this package, which brings in a big dependency tree with it.
Furthermore, I'm not currently sure how much this feature is actually used or why, since we don't use it at all at Sirius.
Lastly, having it seems prone to cause hard-to-debug issues, since you're bypassing the limited API the software already provides, so anything is possible.

- Script upload

Aside from being a security risk, this functionality doesn't seem to be that useful or used, unless other laboratories disagree. Keeping this out for now
at least keeps the environment worker logic simpler.

Lastly, the following are features not present in the original implementations, but that are currently targeted for this project:

- [ ] Testing for multiple queues
- [ ] Endpoints to interact with multiple queues
- [ ] Device bookkeeping for checking current usage in other queues
