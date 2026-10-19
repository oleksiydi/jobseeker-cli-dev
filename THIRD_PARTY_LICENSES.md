# Third-Party Licenses

## Granite embedding experiment

- Model: `ibm-granite/granite-embedding-311m-multilingual-r2`, revision
  `44399559930365213510b1ee2eb15ded83374f0e` (Apache-2.0 model weights).
- Source and tokenizer attribution:
  https://huggingface.co/ibm-granite/granite-embedding-311m-multilingual-r2
- The model card identifies a Gemma 3-derived tokenizer and references the
  Gemma Terms of Use for that tokenizer.
- ONNX weights/tokenizer are downloaded separately into ignored `models/`;
  no model binaries are bundled or enabled in production by this experiment.

## BGE-M3 experiment

- Model: `BAAI/bge-m3`, revision `5617a9f61b028005a4858fdac845db406aefb181`.
- Model weights/license attribution: MIT,
  https://huggingface.co/BAAI/bge-m3
- Official inference package: FlagEmbedding 1.4.2,
  https://github.com/FlagOpen/FlagEmbedding
- Model binaries and the independent CPU experiment environment are installed
  separately into ignored `models/embeddings/`; neither is bundled in Git or
  enabled in production. Exact package versions are retained with the experiment.

## Vacancy document export

- WeasyPrint (installed Python dependency): BSD-3-Clause;
  https://github.com/Kozea/WeasyPrint/blob/main/LICENSE
- Pandoc (separately installed command-line program): GPL-2.0-or-later;
  https://github.com/jgm/pandoc/blob/main/COPYING.md
- System fonts are supplied by the operating system; no font binaries are bundled.

## Reference data (ESCO and BA/DKZ)

This service uses the ESCO classification of the European Commission.

- ESCO data reuse terms: https://esco.ec.europa.eu/en/about-esco/faq?page=1
- The importer preserves original ESCO responses and records the requested version.
- BA/DKZ source: Downloadportal der Bundesagentur für Arbeit, www.arbeitsagentur.de.
- BA/DKZ terms: https://www.arbeitsagentur.de/institutionen/dkz-downloadportal/nutzungsbedingungen-dkz
- BA/DKZ source content is preserved unchanged; retrieval dates and source credit are stored in MongoDB manifests.
- Reference datasets are downloaded separately and are not included in this repository.
- No ESCO API implementation software is incorporated; the importer uses the public web service.

## Mermaid

- Component: `docs/javascripts/mermaid.min.js`
- Project: Mermaid
- Source: https://github.com/mermaid-js/mermaid
- License: MIT

License text (MIT):

```text
MIT License

Copyright (c) 2014-2024 Knut Sveidqvist

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
