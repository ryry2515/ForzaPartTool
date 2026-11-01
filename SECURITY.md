# Security

## What the editor does and does not expose

`toolkit/carbin_editor/server.py` is a local web app. It can read your game and DLC folders and write into the car archives, the output folders and paths you give it, so it protects itself:

- It listens on `127.0.0.1` only, never on the network.
- Every request must carry a random secret that is created each time the editor starts. The address printed in the terminal contains it (`?token=...`); the first visit swaps it for an `HttpOnly`, `SameSite=Strict` cookie, so other web pages you have open cannot use the editor (cross-site request forgery).
- The `Host` header must be `127.0.0.1` or `localhost` with the right port (defeats DNS rebinding) and a foreign `Origin` header is refused.
- Request bodies are limited to 512 MB. Download file names are sanitised. Static files come from a fixed list and the bundled `vendor/` folder, with path escapes refused. The page sets a strict Content-Security-Policy: it loads only its own scripts, so it makes no request to any other site (three.js is bundled, not loaded from a CDN).
- Writing into an archive only touches the one member you ask for, keeps a `.bak`, refuses while Xenia is running, and only accepts archives the library actually found.
- The page never builds HTML from file contents (no `innerHTML`); part and material names from a car file are shown as text.

`python toolkit/selftest.py <game folder>` runs checks of these refusals (no secret, wrong secret, foreign host, foreign origin, oversized body, path escape).

## Limits you should know

- The editor and the toolkit parse files with the formats' own parsers. A damaged or hostile `.carbin`, `.xds`, `.gr2`, STFS or zip can make a command fail or use a lot of memory; open files from sources you trust.
- "Save to path", "Open path" and the output folder of "Export as DLC" deliberately take any path on your machine. They need the secret like everything else, but anyone who has the secret (the address in your terminal) can use them; do not share that address.
- Never run the editor on a shared machine or expose its port with a tunnel.
- Test builds on a copy of your save: removing a DLC that holds a car you bought breaks the save.

## Reporting

Please report problems with GitHub's private vulnerability reporting ("Security" tab, "Report a vulnerability") instead of a public issue.