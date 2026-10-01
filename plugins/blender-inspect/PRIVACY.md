# Privacy and data handling

Effective September 29, 2026.

Blender Inspect provides local inspection of Blender scenes: mesh defect
audits, diagnostic renders and depth reports. It does not operate a remote
processing service, collect telemetry, or require a plugin account, API key, or
password.

## Local processing and outputs

`.blend` files are opened by a Blender executable on the machine running the
tools, always with `--factory-startup --background`, so user add-ons and
preferences are not loaded. Commands can write JSON reports, rendered images,
depth arrays and EXR files to local storage. Outputs can contain file paths,
object names, coordinates and information derived from the input scenes. These
files remain until you or your host application remove them.

Tool results are returned to the calling agent over local standard output. Your
agent application may send tool results, rendered images, or other conversation
content to its model provider under that application's settings and privacy
policy. The plugin does not control that transmission or the provider's
retention practices.

## Network access

The tools make no network requests. Installing the plugin contacts the
configured source host. Blender itself is installed and updated by you, outside
this plugin.

## Contact

Questions about this policy can be raised as an issue at
https://github.com/bsmi021/pil-agent-plugin/issues.
