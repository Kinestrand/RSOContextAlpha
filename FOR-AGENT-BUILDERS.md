# RSO Context, for people who already ship agents

This is a positioning note, not a specification. It assumes you already know agent loops, tools/skills, retrieval, and “second brains.” It does not describe internal algorithms.

## What it is

RSO Context is a **local evidence ledger** that agents share. It is not the agent’s memory, not a notes vault, and not a skill full of facts.

The service sits **outside the model**. It indexes bounded project folders you registered. Agents are clients: they ask for context, they do not own the store, the parsing rules, or the trust decisions. Chat is not a source. If nobody wrote it in a file, it did not happen as far as RSO is concerned.

A typical skill *is* the knowledge (“remember that we use X”). RSO’s skill only says **when** to call the ledger and how to read what came back. Codex, Claude, Graft-using coding agents, and a local script can all hit the same index.

## Why that is important

Most agent memory products optimize for **recall**: what did we talk about, what is similar, what should the model remember next time.

RSO optimizes for **production discipline**: what is on the current plate, which take is current, and whether a named person signed it.

That sounds small until you have run two agents on the same repo. If each one keeps its own summary, you get three truths by Thursday. If they share a ledger of *files*, you get one corpus and an argument you can point at.

## Versus a second brain

A second brain (Obsidian, Notion AI, Mem, ChatGPT/Claude memory, most “agent memory” plugins) stores captures and later retrieves related stuff. The store becomes the memory. A summary of last Tuesday’s chat often comes back as if it were the file.

RSO refuses that promotion:

- The **files** are the memory. The index is a copy of those files plus structure, not a diary of the model.
- **Observed** means the current source contains that sentence. That is all. Ten retrievals and three agents agreeing still do not make it true.
- **Verified / disputed / superseded** only happens when a named person (or an explicit validator they authorized) records it. Frequency is not proof.
- Conflicts stay visible. The packet can say it does not know. It is not allowed to merge two disagreeing sources into one “brain fact.”

If you wanted a second brain, you would ingest chat and embeddings. Alpha does neither, on purpose.

## Versus Graft (and other code-structure tools)

Graft answers “where is this function, who calls it, what breaks if I change it.” That is a **scene graph** for code.

RSO answers “what did the sources say, has anyone signed that, and which project is this.” That is **conform and approval**.

They work together and must not merge. A Graft card is a derived view. If you indexed those cards as if they were originals, a summary would launder itself into evidence. Mixed questions stay source-labeled: Graft for location, RSO for policy and history. Repeating a Graft sentence in chat does not verify it.

Other repo maps (Aider’s map, grep, language servers) sit on the Graft side of that line. RAG-over-the-monorepo sits in the middle and usually pretends similarity is authority. RSO will not.

## Versus ordinary RAG and “just dump the repo”

Dumping files into the prompt is honest and expensive. Embedding search is cheap and blurry. Both still leave the **model** as the judge of what is true.

RSO’s job is to hand the agent a small, checkable packet: this project, these current sources, these claims, any named validations, a hash of the packet. The agent still writes the answer. The ledger does not update the model’s weights and does not make the LLM deterministic. It makes the **inputs** inspectable.

## The computer-graphics intuition

The idea comes from production, not from cognitive science.

In VFX, the 2D plate is the source of truth. A camera solve is a claim *about* the plate. Looking at the take ten times does not approve the shot. A supervisor does. You keep every take. The cut uses the current one. You never average two conflicting plates into one “true” image. Wrong shot is a disaster even if the solve looks pretty.

That is the posture: **plate, take, coverage, named approval.** Other tools ask what the model should remember. This asks what is on the plate and who signed it.

## For CS / data people

Nothing below is a secret sauce. It is why the boring parts matter.

**Authority vs index.** The SQLite file is an index over registered folders, closer to a content-addressed corpus than to a knowledge graph product. Hashes and versions let you say “this span came from this bytes.” Queries use the current version; older versions can remain stored. That is ordinary CAS + history, the same instinct as git objects, not a new kind of database.

**Trust is a stored state, not a score.** Observed / proposed / verified / disputed / superseded is a workflow enum. It is not cosine similarity, not an LLM judge, and not “N agents said so.” If you need a statistic, put it in an audit log. Do not overload it as truth.

**Deterministic retrieval, probabilistic writing.** Database reads, hashes, budgets, and packet hashes can be byte-identical for the same corpus version and settings. Partitioning a messy English request, and the prose the model writes afterward, are not. Keep that boundary honest or you will ship a RAG demo wearing a verification badge.

**Full-text first.** Alpha uses local SQLite FTS rather than embeddings so the hot path has no vendor, no GPU, and no “nearby chunk” theology. Embeddings can be a replaceable secondary index later. They must not become the authority layer.

**Bounded roots.** Indexing a user profile, Documents, or an entire cloud drive is how you contaminate a project with vendor clones and leftover checkouts. Register a folder the way you would register a shot, not a whole facility.

**Shared, local, per person.** The program can be copied. Each person gets their own index. Sharing RSO shares the client and the skill, not someone else’s corpus, unless they explicitly want that file.

**Agents never write truth.** They may propose. Promotion is a human or a deterministic check they already trust (a test, a schema, a named sign-off). That is the same split as CI vs a merge: the bot can run the suite; it does not own main.

## What this is not

Not a second brain. Not Graft. Not ChatGPT memory. Not an embedding database. Not a proof that the model reasoned correctly. Not a replacement for reading the file.

If you only remember one line: **second brains remember the conversation; RSO remembers the files, and only a named person can promote a sentence from “it was on the plate” to “we accepted it.”**
