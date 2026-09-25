# Privacy and data handling

Effective September 25, 2026.

PIL Agent Plugin provides local image measurement, comparison, and optional
Blender geometry and rendering tools. It does not operate a remote processing
service, collect telemetry, or require a plugin account, API key, or password.

## Local processing and outputs

Images, masks, meshes, and locally supplied ONNX models are processed on the
machine running the tools. Commands can write reports, annotated images, renders,
and setup receipts to local storage. Outputs can contain file paths, image
metadata, OCR text, measurements, and information derived from the input files.
These files remain until you or your host application remove them.

The optional MCP adapter uses local standard input/output, not a hosted HTTP
server. It returns tool results to the calling agent. Your agent application may
send tool results, attached images, or other conversation content to its model
provider under that application's settings and privacy policy. The plugin does
not control that transmission or the provider's retention practices.

## Network access during setup

Installing the plugin or dependencies contacts the configured source hosts and
package registries. Bootstrap can invoke uv or pip for Python dependencies and
winget, Homebrew, apt, or dnf for Tesseract. Those services receive normal download
requests, including your network address and requested package information;
configured package-manager authentication is handled by those tools. Plugin
bootstrap does not upload input images or download ONNX model weights.

The optional README schema-validation command downloads a public JSON schema
from agent-plugins.org. It does not upload your manifest or images. Downloads and
any separately configured external tools are subject to their providers' policies.

## Credentials and contact

Do not include secrets in input files or reports you share. Keep package registry
credentials in your package manager's secure configuration, outside this repository.
The plugin does not ask for or store user login credentials.

For questions about this policy, use the
[repository issue tracker](https://github.com/bsmi021/pil-agent-plugin/issues).
Do not post credentials or private input files in public issues.
