"""Site-signal connectors, all writing one normalized table (date, page, source, metric, value):
gsc (Search Console API, service account), ga4 (Data API), cloudflare_bots (Worker logging verified AI-bot requests),
changes (change log written by the site-update loop + crawl diffs), conversions."""
