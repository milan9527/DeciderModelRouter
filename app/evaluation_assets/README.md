These are public evaluation fixtures, not user uploads.

- `red.png`, `blue.png`, `digits.png`, `chart.png`: generated for this project with Pillow. RGB solid colors, the digits `2048`, and two bars with B taller than A. No private data.
- `speech.wav`: project-generated Amazon Nova 2 Sonic English speech: “Hello, welcome to the model gateway.” 24 kHz mono PCM WAV. Generated during the gateway acceptance check on 2026-11-25.
- `flower.mp4`: MDN CC0 sample video, downloaded from https://interactive-examples.mdn.mozilla.net/media/cc0-videos/flower.mp4 . Used for manual video description assessment; this is not a temporal localization benchmark.
- `manifest.json`: byte sizes and SHA-256 hashes.

The evaluator copies fixtures into the signed-in account's private media store before invoking Bedrock. References, expected answers and rubrics are not included in classifier or target prompts.
