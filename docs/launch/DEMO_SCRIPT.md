# Demo Script (for Asciinema / GIF / Video)

Three demos, three formats. Pick what you record. Each one is timed
to land under 60 seconds because that is the attention budget on
Twitter and Hacker News.

## Tooling

```bash
# macOS / Linux
brew install asciinema
cargo install --git https://github.com/asciinema/agg

# Windows (WSL is easiest for asciinema)
# or use OBS Studio + a terminal recording app for direct MP4
```

Recording:

```bash
asciinema rec --idle-time-limit 1 --command "bash" demo.cast
# ...run the demo, exit when done...
agg demo.cast demo.gif --theme monokai --font-size 16 --fps-cap 30
```

Output target: under 5 MB GIF, under 30 MB MP4. Twitter accepts both;
Hacker News embeds GIFs in thumbnails when linked.

---

## Demo 1: The Hero (mutation testing) - 45 seconds

This is the one to lead with on every launch post.

**Setup before recording:**

```bash
# Have these already done off-camera:
pip install llm-rewind
rewind init
export GEMINI_API_KEY=...
cd rewind   # the cloned repo with the demo agent

# Pre-record a session so the demo does not wait on a real API call
rewind record --port 18080 --name demo py tests/agents/gemini_agent.py
# Note the session id; you will reference it below
```

**On-camera script:**

```bash
# Type slowly. Pause for one beat after each prompt return.

$ rewind list

# (table shows the recorded session — let viewer read for 2s)

$ rewind mutate <session-id> --command "py tests/agents/gemini_agent.py"

# (mutation report renders — pause for 5s on the final summary line)
# Survived: 0 | Changed: 0 | Crashed: 5 | Total: 5
```

**Voiceover or caption overlays:**

- Frame 1 (0-3s): "I recorded one call to Gemini."
- Frame 2 (3-10s): "Then I ran `rewind mutate`."
- Frame 3 (10-40s): table renders
- Frame 4 (40-45s): "5 crashes. This agent has no error handling."

---

## Demo 2: Bisect Cause Inference - 30 seconds

Best for the Twitter thread reply tweet.

**Setup:**

You need two sessions that diverge. Easiest way:

```bash
# Session A: baseline
rewind record --port 18080 --name run-good py tests/agents/gemini_agent.py
# capture id as $A

# Session B: edit gemini_agent.py to change the prompt, then record again
sed -i 's/say hi/say goodbye/' tests/agents/gemini_agent.py
rewind record --port 18080 --name run-bad py tests/agents/gemini_agent.py
# capture id as $B

# Revert the agent file
git checkout tests/agents/gemini_agent.py
```

**On-camera script:**

```bash
$ rewind bisect $A $B

# (output shows)
# First divergence at step 0
#   Session A: run-good  ...
#   Session B: run-bad   ...
#   Cause:    prompt_drift
#   Detail:   request body changed between runs (messages, system,
#             or sampling params). Re-check whether the calling code
#             rebuilt the prompt or pulled a new template.
```

**Voiceover / captions:**

- "Two runs. Why do they differ?"
- (pause on `Cause: prompt_drift`)
- "Rewind tells you. Other tools stop at 'step 0 differs'."

---

## Demo 3: Full Loop - 60 seconds

For the blog post hero or the LinkedIn video.

```bash
$ pip install llm-rewind
# (skip the install output; cut to the prompt)

$ rewind init
# Generating CA certificate ...
# ✓ Rewind initialized at ~/.rewind/

$ rewind record py my_agent.py
# ● Recording 7f3a2b9c
# (your agent runs, output shows)
# ✓ Captured 4 LLM call(s) — ~$0.0034 | 2.1s

$ rewind replay 7f3a2b
# ▶ Replaying 7f3a2b (strict mode)
# (same output, no API call)
# ✓ Replay complete — 0.3s (zero LLM cost)

$ rewind mutate 7f3a2b
# (mutation report)
# Survived: 12 | Changed: 5 | Crashed: 3 | Total: 20

$ rewind bisect <good> <bad>
# (cause inference output)
```

End on the bisect Cause line. That is the money shot.

---

## Visual Polish Checklist

- [ ] Clean shell prompt. No usernames, hostnames, or working
      directories with personal info.
- [ ] Wide enough terminal (110+ cols) so the Rich tables do not wrap.
- [ ] One consistent color theme across all demos.
- [ ] Cut dead air between commands; viewers fast-scroll past pauses
      over 1.5s.
- [ ] Add a single-frame title card at the start: "Rewind" + tagline.
- [ ] Add a single-frame end card: GitHub URL + `pip install
      llm-rewind`.

## File Hosting

- GIFs: commit to the repo at `docs/launch/assets/`. Reference with
  raw GitHub URLs in social posts.
- MP4: upload to Twitter directly for the algorithm, also mirror to
  YouTube (unlisted) for stable links.
- Asciinema casts: upload to asciinema.org and embed in blog posts.

## Title and Tagline Variants to Test

The blog post and GIF caption should match. Variants in order of
expected click-through:

1. "Mutation testing for LLM agents found 8 bugs in [popular agent]
   in 4 seconds"
2. "Rewind: bisect cause inference and chaos testing for AI agents"
3. "I built `git bisect` for LLM agents. Here is what broke."

Pick the one with the strongest specific number for HN and Twitter;
use the more abstract one for LinkedIn.
