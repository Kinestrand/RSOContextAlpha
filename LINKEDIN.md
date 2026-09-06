# LinkedIn draft (not posted)

Paste the article below. This is a public-facing posture piece, not a spec.
It does not describe methods, internals, or a build list.

Suggested title: **Agents don't need another second brain. They need a plate.**

---

Most AI “memory” products are built like a second brain.

They remember the conversation. They store summaries. They retrieve whatever looks similar. After a few days with more than one agent on the same project, you don’t have memory. You have three diaries, each sure it is the file.

I come from computer graphics and VFX. In that world the 2D plate is the source of truth. A camera solve is a claim about the plate, not a replacement for it. Watching the take ten times does not approve the shot. A supervisor does. You keep the takes. The cut uses the current one. You never average two conflicting plates into one “true” image. A pretty wrong shot is still wrong.

I have been building **RSO Context** around that posture.

It is a local evidence ledger for AI agents. The files are the memory. Agents are clients of a shared store; they do not each own a private truth. If it was not written in a source, it did not happen. “We retrieved this a lot” and “three models agreed” are not the same thing as a named person signing off.

That split is the whole product instinct:

- **Observed** means the current source contains the sentence. That is all.
- **Accepted** is a human (or a check they already trust) recording a decision.
- Conflicts stay visible. The system is allowed to say it does not know. It is not allowed to merge two disagreeing sources into one brain-fact.

This is a different job from a code map. A map answers where a function lives and who calls it. A ledger answers what the sources said, which project you are in, and whether anyone signed it. Those should not be the same database. If you index a generated summary as if it were an original, the summary launders itself into evidence.

It is also a different job from ordinary RAG. Dumping a repo into the prompt is honest and expensive. Embedding search is cheap and blurry. Both still leave the model as the judge of what is true. I want the *inputs* inspectable: this project, these current sources, these claims, any named sign-off. The model still writes the answer. The ledger does not pretend the writing was proven.

I am not building a second brain, a chat archive, or an embedding store. I am not trying to make the model deterministic. I am trying to make production discipline available to agents: plate, current take, coverage, named approval.

If you hire people who have shipped agents, you already know the failure mode. The demo looks fluent. The second week, nobody can point at the sentence that authorized the change. That is the hole I am working in.

I teach and I still make things. If you care about local, inspectable context for agents — or you have a production problem that looks like this — I am easy to find.

---

Notes for you (do not paste):

- First person, hire-me close, no CLI, no schemas, no algorithms.
- Dropped CS subsections, Graft-by-name internals, SQLite/FTS, packet hashes, and anything that reads like a method.
- “RSO Context” as the project name is positioning, not a how-to.
- This draft stays at problem and posture on purpose. If you want a tighter legal pass before posting, say so.
