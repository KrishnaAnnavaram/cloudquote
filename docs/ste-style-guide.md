# The writing standard: ASD-STE100 Simplified Technical English

Use these rules for every README and for `docs/ste-style-guide.md` in each repository. Copy this file
into the repository as `docs/ste-style-guide.md` and add a **project vocabulary** section (Section 3)
with the technical names and technical verbs of that project.

## 1. The writing rules

### Words

1. Use one word for one meaning, and one meaning for one word. Do not use synonyms for variety.
2. Use a word only as one part of speech. For example, `test` is a noun or a verb, `check` is a verb.
3. Do not use phrasal verbs (`set up`, `carry out`, `find out`, `pick up`, `look up`, `come up with`).
   Use one verb: `prepare`, `do`, `find`, `get`, `make`.
4. Do not use an `-ing` form as a noun or an adjective (`the running job`, `after indexing`).
   Exception: a technical name, a file name, a command or a status value.
5. Do not use contractions (`don't`, `it's`, `can't`). Do not use slang or idioms
   (`out of the box`, `under the hood`, `at a glance`, `gotcha`, `bells and whistles`).
6. Do not use `and/or`. Write `A, B or both`.
7. Do not use `should`, `could`, `would` or `may` for instructions. Use `must` for a rule, the
   imperative for a step and `can` for a possibility.
8. Keep the articles `a`, `an` and `the` in sentences.
9. Do not make a noun cluster of more than three words. A technical name is one word.

### Sentences

1. A procedural sentence (an instruction) has a maximum of **20 words**.
2. A descriptive sentence has a maximum of **25 words**.
3. Write one instruction in one sentence.
4. Use the imperative for an instruction: `Run the tests.` Not `The tests should be run.`
5. Use the active voice. Use the passive voice only when the agent of the action is not important.
6. Use only the simple present, the simple past and the simple future.
7. Put a condition before the instruction: `If the index is stale, build it again.`
8. Do not use semicolons in sentences. Write two sentences.

### Paragraphs, notes and warnings

1. A paragraph has one topic and a maximum of **6 sentences**. Start with the topic sentence.
2. A warning or a caution starts with a clear command. Then it gives the reason.
3. A note gives information. It does not give an instruction.
4. Use a vertical list for a sequence or a set of conditions. Each item of a numbered procedure is one step.

### Tables, headings and diagrams

1. A table cell can be a short phrase. If a cell has a sentence, the sentence obeys the rules.
2. A heading is a noun phrase (`The cost model`) or an imperative (`Run the demo`).
   Do not start a heading with an `-ing` form.
3. A diagram label is a short phrase. Use the same terms as the text.

### What STE does not change

Code, commands, file names, paths, field names, environment variables, status values, enum values,
product names and URLs stay exactly as they are. They are technical names. Put them in backticks.

## 2. General words to replace

| Do not use | Use |
|---|---|
| utilize, leverage | use |
| in order to | to |
| set up | prepare, install, configure |
| carry out, perform | do |
| make sure, ensure | make sure (allowed), or "check that" |
| a lot of, lots of | many, much |
| e.g., i.e. | for example, that is |
| should (instruction) | must (rule) / imperative (step) |
| might, may (possibility) | can |
| very, really, just, simply, easily | (delete) |
| seamless, robust, powerful, blazing | (delete or give a measured fact) |

## 3. Project vocabulary

This section gives the technical names and the technical verbs of cloudquote. The README uses each term with only this meaning.

### 3.1 Technical names (nouns)

| Term | Meaning | Do not use |
|---|---|---|
| **requirement** | The validated `Requirement` structure, or the plain-English text that describes it | spec (except for the `--spec` file), request, needs |
| **requirement text** | The plain-English input | prompt, query, description |
| **assumption** | A default value that the extractor used and reports | guess, inference |
| **catalog** | The versioned JSON file with all SKUs and prices | price list, price table, database |
| **SKU** | One catalog entry with a provider, a kind, a unit and a price | product, offering, instance (for storage) |
| **instance type** | A compute SKU, for example `m5.xlarge` | machine type, VM size, flavour |
| **storage class** | A storage SKU, for example S3 Standard-IA | tier (except `access_tier`), bucket type |
| **access pattern** | The `access` field: `frequent`, `infrequent`, `rare`, `archive` | temperature, hotness |
| **workload** | The `workload` field: `general`, `compute`, `memory`, `burstable` | profile, usage type |
| **provider** | A cloud: `aws` or `gcp` | vendor, platform, cloud (as a noun for one provider) |
| **LLM** | A language model behind the `LLM` interface | AI, model (alone), chatbot |
| **line item** | One `LineItem`: one SKU, quantity, rate, unit pair and billed months | row, entry, charge |
| **unit pair** | The quantity unit and the rate unit of a line item | units, dimensions |
| **rate** | The price for one unit, from the SKU | cost (for a unit price), tariff |
| **monthly cost** | quantity × rate of one line item | monthly price, run rate |
| **monthly total** | The sum of the monthly costs of a quote | bill, invoice |
| **term** | The estimate horizon, `term_months` | duration, period, contract |
| **term total** | The sum of the term costs of a quote | lifetime cost, total cost |
| **billed months** | The months that a line item is charged for | charged period |
| **minimum duration** | The `min_storage_days` of a storage class | minimum period, early-deletion window |
| **quote** | The priced result for one provider | estimate (for one provider), offer |
| **estimate** | The result for all providers, with the cheapest provider | quote (for all providers), report |
| **complete quote** | A quote with no unavailable part | full quote, valid quote |
| **egress** | Data sent from the cloud to the internet | outbound traffic, bandwidth, download |
| **retrieval** | Data read back from a storage class | restore, read cost |
| **extractor** | `RuleBasedExtractor` or `LLMExtractor` | parser (except for the rules), reader |
| **matcher** | The functions in `matcher.py` | recommender, selector |
| **explanation** | The text summary of an estimate | narrative, description |

### 3.2 Technical verbs

| Verb | Meaning |
|---|---|
| **extract** | Change a requirement text into a `Requirement` |
| **validate** | Check a structure against its pydantic schema |
| **match** | Select the cheapest SKU that meets each constraint |
| **price** | Make the line items of a quote |
| **verify** | Check each line item against its SKU in the catalog |
| **explain** | Make the text summary of an estimate |
| **record** | Add a text to the assumptions or the warnings |
| **round** | Change an amount to cents with `ROUND_HALF_UP` |
| **evaluate** | Measure the extractor, the matcher and the cost on the eval set |
