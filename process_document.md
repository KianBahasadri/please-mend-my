## Project Overview

### Purpose
I have a lifelong habit of putting people on the spot, bringing up moral and philosophical questions in everyday conversation to test whether I can point out some flaw in their beliefs. [**pleasemendmy.com**](https://pleasemendmy.com) is kind of an intimate project because it's an extension of that habit. I wanted to challenge you (yes, you 🫵) on how you use technology every day.

Today's hardware and browsers can hold a searchable encyclopedia of practical knowledge on the device and answer in milliseconds, so that tool should already exist and be the default. Instead, advertising incentives, platform politics, and pressure to route basic questions through AI products have left us with slow, bloated tools. As designers, there should be nothing more disappointing than seeing our friends and family depend on tools that are slower, less economical, return AI answers that can change from one run to the next, and are increasingly becoming subscription services.

This project is meant to be kind of punk and countercultural. I built it to criticize slow, wasteful defaults by showing the alternative, and I've enjoyed it so much that I've decided to maintain and improve it for future use!

### Audience and Goals
The audience is anyone seeking practical or reference knowledge on any topic: repairs, medicine, first aid, cooking, software, sustainability, childcare, or relationships. Rather than stopping at physical repair, the goal is to supply all the expert-vetted, reputable human-written information we can source, answer locally in milliseconds without live AI, and avoid wasted transfers.

## Research and Insights

A mentor conversation framed the problem: young adults used to call their parents for help with eating well, mending broken items, or caring for children. General advice was easy to find, but situational advice was hard.

Today people use Google, AI, or manual source hunting. AI suits open-ended problems, but all three fail everyday lookup:

- **Search engines:** Sponsored links, SEO filler, and ads bury instructions. Queries tie into user profiles and tracking, trading data privacy for ad targeting while shaping results into personalized information bubbles.
- **AI chat:** Users wait while a remote model streams verbose, non-deterministic answers with hard-to-verify sources. Capable tiers increasingly require paid subscriptions, while prompts and personal context are logged to user profiles.
- **Manual source hunting:** Going straight to authentic manuals, wikis, or archives avoids ads, profiling, and AI guesswork, but knowledge sits scattered across clunky directories. Knowing where to look and digging through separate sites is harder than Google or AI, so despite its upsides, rarely anybody does it.

## Initial Mockups and Prototypes

To test real typing speed, I skipped static wireframes and prototyped directly in browser code. The project started as an idea for an autonomous agent that resolves problems *for you*, inspiring the prompt “*please, mend my* \_\_\_”. Real scenarios quickly exposed the flaw: for a burn, a leaking pipe, or a slipped chain, an agent cannot step in for you and only slows things down when you need clear human instructions.

Keeping that sentence prompt, the first prototype searched an offline iFixit archive locally above category filters and result cards. Local search felt instant, but visitors hesitated at the blank prompt, hardware repair felt too narrow, and scanning cards still felt like a search engine.

## Iterations and Feedback

Feedback from four mentors helped address those early flaws:

- **Atmosphere, wordplay, and onboarding:** Drew Lyford helped replace the early backdrop with a spotlight, and Prof. Mohammad Ridwan Kabir suggested animating example words into the idle prompt so visitors immediately see they should type too. Font swaps also evolved into physical mending textures (stitched thread, blueprints, and tool-shaped letters).
- **Repair tool to general encyclopedia:** Building on mentor discussions, the iFixit prototype expanded to WikiMed, U.S. Army First Aid, Appropedia, and wikiHow (435,213 items), using offline postings, stems, and aliases for fast browser search.
- **Cards to inline answer and triage:** Category filters and result cards were cut; bare nouns gained follow-up prompts with clickable part diagrams, and the top guide moved inline.
- **Offline restructuring:** Because HTML varies across sources, a build pipeline ran an offline model on a 100-guide pilot to reorganize articles into uniform tools, warnings, and steps without runtime AI.

## Final Product and Outcomes

- **Everyday language and part lookup:** Users rarely know part names (a bicycle *derailleur*) and type symptoms (*“it's not turning on”*). The prompt strips filler, keeps negation, asks *“What's wrong with your [noun]?”* for bare nouns, and lets users click parts on available diagrams or lists.
- **Immediate inline guides:** Search ranks locally as you type, showing the top match inline with five side alternatives. Pilot guides show safety notices, tools, materials, and steps; others render cleaned source HTML with attribution and source links.
- **Private, low-request lookup:** Local search keeps queries private on-device with no profiling. Once loaded, the site makes almost no repeat requests, never refetches cached index data or guides, keeps opened guides working offline, and minimizes server bandwidth and compute.

## Figma

Figma was not used. (I never learned rip.)

## Citations and AI Use

### Visuals
No AI was used to create visuals. Diagrams, SVGs, and images come from Wikimedia Commons and Openclipart under open licenses, with source hashes and credits recorded.

### Data Sources
- **Kiwix library:** ZIM archives of WikiMed (CC BY-SA 4.0), iFixit (CC BY-NC-SA 3.0), and Appropedia (CC BY-SA 3.0).
- **Other:** U.S. Army FM 4-25.11 First Aid (public domain), the wikiHow corpus (CC BY-NC-SA 3.0), and Open English WordNet (CC BY 4.0).

### AI Use
- **Code and writing:** All code was written by LLM agents, which also helped edit this document.
- **Offline text:** `qwen/qwen3.6-35b-a3b` batch-processed pilot articles into a strict schema and mapped open-source SVGs.
