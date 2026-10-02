# Third-party components

The `extension/package-lock.json` records the exact upstream source and
integrity hash of every JavaScript dependency. Native packages include those
dependencies with their license files intact:

- `node-roon-api`, `node-roon-api-transport`, `node-roon-api-image`, and
  `node-roon-api-status`: Roon Labs, Apache-2.0.
- `node-uuid`: Robert Kieffer and contributors, MIT.
- `ws`: its authors and contributors, MIT.

Roon Bridge is downloaded separately from Roon Labs when the user enables the
plugin. Its proprietary distribution and terms remain Roon Labs' responsibility;
no Bridge binary is redistributed in this repository or its build artifacts.
