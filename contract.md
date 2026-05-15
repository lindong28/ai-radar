# AIHot Parity Contract for Simulated User Tests

This file is an input contract for simulated user testing of AI Radar against AIHot.
Use it together with live visual inspection of:

- `https://aihot.virxact.com/` vs `https://aiplanet.live/`
- `https://aihot.virxact.com/all` vs `https://aiplanet.live/all`
- `https://aihot.virxact.com/daily` vs `https://aiplanet.live/daily`

## Feed Card Contract

### Selected badge

- A selected item should show `精选` only in the score/badge area beside the numeric score.
- The topic tag row must not include another `精选` tag.
- When checking this visually, count only topic/category tags, not the score badge.

### Score display

- Selected items show a `精选` badge plus a numeric score.
- Non-selected but scored items show the numeric score without an extra `评分` text label.
- `/all` should include scores for scored items, not only `/`.

### Recommendation reason

- Selected items in both `/` and `/all` should show a visible recommendation reason.
- Non-selected items in `/all` do not need a recommendation reason.
- If `/all` contains items that also appear in the latest selected feed, those items must carry the selected marker and reason.

### Media display

- Content media is different from source avatars.
- Do not count author/source avatars as article media.
- If the API returns `media_assets` for `/all`, the page should render content images for those items.
- `/all` should not silently drop all content images while `/` shows many images.

### X/social card text style

- AIHot treats many X/social posts as body text, not as a heavy article headline.
- For X/social cards, AI Radar should avoid a stronger title treatment than AIHot.
- Check computed or visible style: X/social primary text should read closer to `14px`, regular weight, relaxed line height, not a `15px` bold headline.
- RSS/article cards can keep a stronger article-title style.

### Natural click target

- The title or primary body text should be the natural external-link target.
- Content images may also link to the original item.
- Avoid adding extra media/open buttons when AIHot solves the same task through the title/body/image click target.

## Page-Level Contract

### `/` selected feed

- Every card is selected and should show score plus recommendation reason.
- Topic tags should contain only content categories/entities, not duplicate selected state.
- X/social card media should be constrained and should not dominate the viewport.

### `/all` full feed

- The page is not a text-only compact list.
- It should preserve AIHot's card affordances: media when available, numeric scores when available, selected marker for selected items, and selected recommendation reasons.
- It is acceptable for non-selected items to have no reason.
- It should still preserve scanability: first viewport should show multiple cards, and media should not consume most of the viewport.

## Suggested Evidence to Capture

- Screenshot of both sites at desktop `1440x900` for `/` and `/all`.
- DOM metrics for the first 10 cards:
  - card class
  - source kind
  - title/body computed font size, weight, and line height
  - topic tag texts
  - score badge text
  - selected badge presence
  - recommendation reason presence
  - content media count, excluding avatars
- API comparison for AI Radar:
  - `/api/v1/curated`
  - `/api/v1/timeline?limit=40&page=1`
  - overlap count by item id
  - for overlap items, verify timeline includes rank/selected score/reason

## Common False Positives

- Counting source avatars as content media.
- Treating “has a score in API” as enough when the score is not visible in UI.
- Treating “has selected item in `/curated`” as enough when `/all` loses selected metadata.
- Checking only one page. `/` and `/all` must be compared separately because they use different data and render paths.
