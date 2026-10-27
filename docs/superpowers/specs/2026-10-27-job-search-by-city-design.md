# Job search that respects the caller's city — design

Issue: #455 (children #441, #476, #520), with Blue-Dots-Economy/signals-search#182 and #183.

Status: approaches for review. No approach has been chosen yet.

## Problem

A caller says "welder jobs in Bengaluru". The voice bot reads out welder jobs, but most of them are in Uttar Pradesh, 1,500 km or more away. The bot has no reliable way to tell, so it either offers far-away jobs as if they were local, or names a city the job is not in.

The goal is a search that returns jobs matching **both** the trade and the city the caller named, and that lets the bot say honestly when there are none in that city.

## How it works today

### AI Diffusion DPG

- In the `job_match` step, AI Diffusion DPG builds one sentence, `"{trade} jobs in {city}"`, and sends it to signals-search as free text (`textSearch`), asking for 5 results.
- It sends no city filter, no pay filter and no minimum relevance score.
- The bot's prompt tells the LLM to check each row's trade and city itself, because "the search ranks, it does not filter".

### signals-search

- With free text only, signals-search turns the text into a vector and ranks every live job by similarity to it. Nothing filters on the city.
- **The job's location is not part of what is ranked.** In the `up-gzb` network schema, `jobProviderLocation` is now public, but it is not marked `vectorize`. A job's vector is built from its role, title, company, qualifications and similar fields, never its location. So "in Bengaluru" in the query has nothing in the jobs to match against, and it only adds noise.
- **What already exists:**
  - signals-search already supports a location filter: a point (latitude and longitude) plus a radius in metres. It also supports nearest-first ordering and filters on job fields, such as `salaryMax >= 30000`.
  - The filter needs coordinates. It does not accept a city name.
- **Jobs already have coordinates.** Signals-DPG turns each job's location text into coordinates when the job is saved. It uses Google's geocoding service with a Redis cache.

## Measurements on UAT

Taken on 27 Oct 2026 against `signals.bluedotseconomy.org`, read-only, on all 468 live jobs.

### The data

| | Jobs |
| --- | --- |
| Live jobs | 468 |
| With coordinates | 454 (97%) |
| Without coordinates | 14 (3%) |
| Location text public (not masked) | 467 |

Jobs are concentrated around Bengaluru (133), Delhi NCR (69), Lucknow (63), Muzaffarnagar, Varanasi and Kanpur.

### Free text only, against text plus a 40 km radius

"Right trade, in the city" is the number of jobs of that trade within 40 km of the city centre, counted from the full job list.

| Caller asks for | Right trade, in the city | Free text "X jobs in City": top 5 in the city | Trade text + 40 km radius: top 5 in the city | Trade text + 40 km radius: top 5 of the right trade |
| --- | --- | --- | --- | --- |
| Welder, Bengaluru | 5 | 1 | 5 | 5 |
| Electrician, Delhi | 2 | 1 | 5 | 2 (+1 EV charging technician) |
| Carpenter, Lucknow | 2 | 0 | 5 | 2 |
| Mason, Ghaziabad | 3 | 1 | 5 | 3 |
| Cook, Bengaluru | 0 | 0 | 5 | 0 |
| Welder, Chennai | 0 | 0 | 3 | 0 |

What this shows:

1. **The city in free text is close to ignored.** For "Carpenter jobs in Lucknow", all 5 results were in Bengaluru, about 1,600 km away. For "Welder jobs in Bengaluru", the top 3 were in Muzaffarnagar, Ghaziabad and Kanpur.
2. **A radius filter fixes the city completely.** Every row was within 40 km, and the right trade always ranked first.
3. **When the city runs out of the trade, other trades fill the list.**
   - Carpenter in Lucknow returned 2 carpenters, then 3 welders.
   - Cook in Bengaluru returned no cooks; it returned a turner, kitchen helpers and a boiler operator.
   - Right-trade rows scored 0.47–0.68. Wrong-trade rows scored 0.31–0.43.
   - So any location approach also needs a **minimum relevance score**. Without it, "no cook jobs in Bengaluru" comes back as 5 unrelated jobs.
   - The cut-off depends on the query, so it has to be tuned on more trades than these six.

### Searching near a saved profile

A live seeker profile with trade "Welder" and coordinates near Muzaffarnagar was used as the starting point, with a 40 km radius and the text "Welder". The search returned exactly 5 welder jobs in Muzaffarnagar, all within 22 km. The mechanism works on UAT today.

## Approaches, best first

### 1. Send the city name to signals-search, which turns it into a location and filters by radius (recommended)

**How it works.**
1. AI Diffusion DPG sends the trade as the search text, and the city name as a separate new field, for example `place: "Bengaluru"`.
2. signals-search turns the name into coordinates using the same Google geocoding and Redis cache that Signals-DPG already uses when saving a job.
3. signals-search then applies its existing radius filter.

The bot never handles coordinates or a Google key.

**Why it is first.**
- **The filter is already there and proven.** The location filter exists and the measurements above show it works. The only new piece is turning a name into a point.
- **Close to zero latency and cost.** City names repeat constantly. After the first caller says "Bengaluru", every later lookup is a cache hit.
- **One owner, one key, one cache.** The geocoding stays in the Signals services, with one Google key and one cache. AI Diffusion DPG gains no new secret.
- **Other clients benefit.** Raya and the Signals UI can use the same field with no extra work.
- **The bot can speak a place honestly.** The response already carries each job's distance from the city, so the bot can say "12 km from Bengaluru". Each job's location text is now public, so it can also name the job's own city.

**Limits.**
- **It is a new field on the `/v1/search` API and needs API approval.** The request field, its errors (for example, a place that cannot be found), and the default radius have to be agreed first.
- **signals-search needs geocoding access.** Either it gets the Google key and the cache, or Signals-DPG exposes its geocoder to it. Signals-DPG has no such endpoint today, so that would be another new internal API.
- **Ambiguous place names.** Aurangabad, for example, exists in both Bihar and Maharashtra. Lookups must be limited to India, and an ambiguous or unknown place must come back as a clear error, not a guess.
- **A city is not a circle.** The radius needs a default (signals-search's default today is 30 km) and a rule for widening it when nothing is found.
- Shares the common limits below.

**What changes.**
- signals-search: the new field, the geocoding call and the cache.
- AI Diffusion DPG:
  - send the trade and the city separately;
  - set a minimum relevance score;
  - when nothing is found, widen the radius or offer the nearest job with its distance.

### 2. Create the profile first, then search near the profile's location

**How it works.**
1. Move profile creation before the job search: collect the trade and the location, then save the profile.
2. Signals-DPG already turns the profile's location into coordinates when it saves it.
3. AI Diffusion DPG then searches with the profile as the starting point and a radius. signals-search takes the centre from the profile's stored coordinates.

**Why it is second.** No new API and no geocoding in AI Diffusion DPG. It works on UAT today, as measured above.

**Limits.**
- **Slower to the first job.** The caller gives name and profile details before hearing any job. The call gets longer, more callers may drop off, and profiles get created for callers who were only browsing.
- **The new profile may not be searchable yet.** A profile reaches the search index through a background worker. A search right after saving can fail with "anchor not found" until the profile is indexed. The bot needs a retry, and this delay has **not yet been measured** on UAT.
- **The location lookup at save time is best-effort.** If geocoding fails, the profile has no coordinates and the search returns "anchor has no location". The bot needs a fallback.
- **The search text stops ranking.** When a profile is the starting point:
  - the profile's own vector decides the order;
  - the text becomes a strict, word-by-word "must contain" filter;
  - so only the plain trade word can be sent, and close variants miss ("welding" does not contain "welder").
- **A different city means updating the profile first.** "दिल्ली में देखिए" ("look in Delhi") has to change the saved location before searching. The same goes for a different trade.
- **It only covers callers who are willing to create a profile.**
- Shares the common limits below.

**What changes.** AI Diffusion DPG only: the call-flow order, the search request, retries and fallbacks.

### 3. AI Diffusion DPG turns the city into coordinates itself, then searches by radius

**How it works.** AI Diffusion DPG calls Google's geocoding service for the city the caller named, then sends the coordinates and a radius to signals-search. signals-search needs no change.

**Why it is third.** It gives the same search results as approach 1, without a signals-search change. The cost is that the geocoding and its risks move into the voice stack.

**Limits.**
- **Latency.** A geocoding call adds roughly 100–300 ms in the voice path on a cache miss. AI Diffusion DPG would need its own cache.
- **API key.** A new paid key inside the voice stack, which has already had one key compromise.
- **Cost.** Google bills per geocoding request. That is small with a cache and grows with every uncached city.
- **Duplicated work.** Signals-DPG already has the same geocoder, cache and India restriction. This builds a second copy that other clients cannot use.
- **Ambiguous place names**, the same as approach 1, now handled in AI Diffusion DPG.
- Shares the common limits below.

**What changes.** AI Diffusion DPG only: a geocoding step before `fetch_jobs`, a cache, a new secret, and the search request.

### 4. Add the job's location to what signals-search ranks on

**How it works.** Mark `jobProviderLocation` as `vectorize: true` in the network schema, so a job's location text becomes part of its vector. This is now allowed, because the field is public. The query "welder jobs in Bengaluru" would then score Bengaluru jobs a little higher.

**Why it is fourth.** It is the cheapest change: one schema flag and a one-time rebuild of the index. But it improves the ranking without making the results respect the city.

**Limits.**
- **It ranks, it does not filter.** The location becomes one line in the job's text, next to the role (counted twice) and about 20 other fields. Bengaluru jobs move up, but jobs from other cities can still fill the top 5. The search still cannot say "there are none in your city".
- **No sense of distance.** The model (`BAAI/bge-m3`) may treat "Bangalore" and "Bengaluru" as close, which helps. It may also treat Mysuru as close to Bengaluru. It has no notion that Ghaziabad is 500 km from Lucknow, which is the #520 problem.
- **Full addresses dilute it.** The field holds "Plot 12, 2nd Cross, Hoodi, Bengaluru 560048", not just a city. The street, locality and pincode weaken the city's effect.
- **Existing jobs are not re-indexed on their own.** signals-search only re-indexes items that changed. The whole index must be rebuilt once, by changing the index version, which re-embeds every item.
- **It changes ranking for every client of the search**, including the map and list views in the Signals UI, not only the voice bot.
- **The effect is not measured.** It needs a test index before anyone relies on it.

**What changes.** The network schema, plus a one-time index rebuild. It could be added later on top of approach 1 or 2 to sharpen ranking, but it cannot replace them.

### 5. Filter on the job's location text

**How it works.** Filter jobs whose `jobProviderLocation` text contains the city name.

**Why it is last.** signals-search's filters today are exact: "equals" and "is one of". "Plot 12, Hoodi, Bengaluru 560048" never equals "Bengaluru". So this needs a new "text contains" filter in signals-search, or a separate structured city field with a form change and a back-fill of every existing job. Even then:

**Limits.**
- **Spellings vary.** Bangalore, Bengaluru and BLR are all in use, and the caller's spelling may differ from the job's.
- **Some addresses have no city.** They have only a locality or a pincode, and never match.
- **False matches.** "Bengaluru Road, Hosur" matches Bengaluru.
- **City boundaries are hard edges.** A job just outside the city (Hoskote, for Bengaluru) is missed, even if it is 25 km away.
- **The bot already has a city to speak.** Now that the location text is public, the bot can read it from each job. That was this approach's main remaining benefit.

**What changes.** signals-search gains a new filter type, or the schema and forms gain a city field with a data back-fill.

## Common limits

These apply to approaches 1, 2 and 3, which all filter by radius.

- **Jobs without coordinates disappear.** A job with no coordinates can never be inside a radius. On UAT that is 14 of 468 jobs (3%). 13 of them have location text that did not resolve to coordinates, and 1 has no location at all. Those jobs need re-geocoding or fixing at the source, or they are invisible to voice callers.
- **The trade still needs a floor.** As measured, once a city's jobs of the asked trade run out, other trades fill the list. A minimum relevance score is needed, so "no cook jobs in Bengaluru" is said as exactly that. The cut-off needs tuning on more trades.
- **A radius needs a widening rule.** When nothing is found within the default radius, the bot should either widen it or offer the nearest job with its distance ("the nearest welder job is about 70 km away"). That should be a deliberate step, not a silent fallback.

## Open questions for review

1. Is a new `place` field on signals-search's `/v1/search` acceptable (approach 1)? If yes, does signals-search get its own Google key, or call a new internal geocoding endpoint on Signals-DPG?
2. Is moving profile creation before the job search acceptable as a product change (approach 2)?
3. What is the default radius, and how far should the bot widen it before offering the nearest job?
4. How should the 14 jobs without coordinates be handled?
