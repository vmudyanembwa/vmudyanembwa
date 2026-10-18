# PDF.js (vendored)

Mozilla PDF.js **4.10.38**, from the npm package `pdfjs-dist@4.10.38`
(tarball sha256 `1011b38553532d7078c59f26b15a471f8dae00f101b60e2add9b8511737a1ce0`).
Licence: Apache-2.0 (see `LICENSE`).

Only `build/pdf.min.mjs` and `build/pdf.worker.min.mjs` are included. They are copied into Margin
so PDFs open offline. Margin loads them only when a PDF is opened, with `isEvalSupported: false`
(defence in depth for CVE-2024-4367, which this version already fixes).

To update: `npm pack pdfjs-dist@<version>`, copy the two files and `LICENSE`, update this note,
and re-run the PDF tests in `tests/test_margin.py`.
