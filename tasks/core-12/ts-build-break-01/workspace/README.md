# shapes-workspace

A two package TypeScript workspace built with project references.

`packages/core` owns the shape catalogue. `packages/app` renders a report from it.

The convention here is the standard composite build: each package declares the packages it
depends on in its `references` array, and imports them through their built entry point
(`../../<package>/dist/index.js`). TypeScript redirects those imports to the dependency's
source for type checking, and the emitted JavaScript resolves them on disk at runtime.

```
sh build.sh
node packages/app/dist/main.js
```

Expected output:

```
rectangle has 4 sides and an area of 12.00
circle has 0 sides and an area of 12.57
```

`strict` is on for the whole workspace and stays on. There is no node_modules and no
network: `tsc` is installed globally in the image.
