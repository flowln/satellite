# Satellite

Satellite is a server intended for running [Bluesky](https://blueskyproject.io/) plans on remote infrastructure, controlled by an HTTP API.

It builds upon the features of [bluesky-queueserver](https://github.com/bluesky/bluesky-queueserver),
[bluesky-httpserver](https://github.com/bluesky/bluesky-httpserver), and [bluesky-queueserver-api](https://github.com/bluesky/bluesky-queueserver-api),
striving to be compatible with it wherever it makes sense to.

## Missing features at the moment

These are the missing features in relation to the original implementations, that are currently targeted for development in the near future:

- [X] Authentication / Authorization
  - Currently, the basic infrastructure for both of them is implemented. All authorization modes that `bluesky-httpserver` supports are also fully added.
    However, not all authentication providers are implemented, and perhaps that is better left for a common repository reusable by various projects,
    as some in the community have argued for. So, for now, authentication support might be added on a necessity basis.
  - Additionally, the current implementation is entirely agnostic to multiple queues. Perhaps a better approach for the future is to have system-wide
    authentication, but queue-wide authorization methods. How well that'd work will depend on user feedback most likely.
- [ ] Support for QueueServer's annotation decorator
- [X] Load queue / history state at startup
- [ ] Python library / Command-line interface
  - [X] Python library (Sync and Async versions)
  - [ ] Command-line utility (TODO: Investigate usefulness. Maybe this can be targeted as a devops support tool, not an end-user utility)
- [X] Lock key support
- [ ] ZMQ endpoints (TODO: Validate whether this makes sense to add, or is too big a burden for too little usage, I have no idea who uses it directly)
- [ ] HTTP API configuration (i.e. HTTPS support and configure 200 error responses with `success=False` or 4XX responses)
- [ ] WebSocket endpoints
- [X] Console API
- [ ] Task support
- [X] Instructions support
- [X] Loop mode
- [X] Batch endpoints
- [X] Queue autostart
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
