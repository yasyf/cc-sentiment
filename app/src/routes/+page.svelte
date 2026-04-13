<script lang="ts">
	import SentimentTimeline from '$lib/charts/SentimentTimeline.svelte';
	import HourlyHeatmap from '$lib/charts/HourlyHeatmap.svelte';
	import WeekdayBar from '$lib/charts/WeekdayBar.svelte';
	import DistributionHistogram from '$lib/charts/DistributionHistogram.svelte';
	import type { PageProps } from './$types.js';

	let { data }: PageProps = $props();

	const lastUpdated = $derived(
		new Date(data.last_updated).toLocaleString('en-US', {
			month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit'
		})
	);

	const overallAvg = $derived.by(() => {
		const total = data.distribution.reduce((s, d) => s + d.count, 0);
		if (total === 0) return 0;
		return data.distribution.reduce((s, d) => s + d.score * d.count, 0) / total;
	});

	const sentimentLabel = $derived.by(() => {
		if (overallAvg < 1.5) return { text: 'Frustrated', color: 'text-sentiment-1' };
		if (overallAvg < 2.5) return { text: 'Annoyed', color: 'text-sentiment-2' };
		if (overallAvg < 3.5) return { text: 'Neutral', color: 'text-sentiment-3' };
		if (overallAvg < 4.5) return { text: 'Satisfied', color: 'text-sentiment-4' };
		return { text: 'Delighted', color: 'text-sentiment-5' };
	});

	function fmtHour(h: number): string {
		const ampm = h < 12 ? 'AM' : 'PM';
		const display = h === 0 ? 12 : h > 12 ? h - 12 : h;
		return `${display} ${ampm}`;
	}

	const peakUsageHour = $derived.by(() => {
		if (data.hourly.length === 0) return null;
		const peak = data.hourly.toSorted((a, b) => b.count - a.count)[0];
		return { label: fmtHour(peak.hour), count: peak.count, score: peak.avg_score };
	});

	const worstSentimentHour = $derived.by(() => {
		const significant = data.hourly.filter((h) => h.count >= 5);
		if (significant.length === 0) return null;
		const worst = significant.toSorted((a, b) => a.avg_score - b.avg_score)[0];
		return { label: fmtHour(worst.hour), score: worst.avg_score, count: worst.count };
	});

	const busiestDay = $derived.by(() => {
		if (data.weekday.length === 0) return null;
		const days = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
		const peak = data.weekday.toSorted((a, b) => b.count - a.count)[0];
		return { label: days[peak.dow], count: peak.count, score: peak.avg_score };
	});
</script>

<svelte:head>
	<title>cc-sentiment -- Claude Code sentiment trends</title>
	<meta name="description" content="Crowdsourced sentiment analysis of Claude Code sessions. {data.total_records} records from real developers. See how frustration correlates with peak usage hours." />
	<meta property="og:title" content="cc-sentiment" />
	<meta property="og:description" content="Overall score: {overallAvg.toFixed(1)}/5 across {data.total_records.toLocaleString()} records. Peak frustration at {worstSentimentHour?.label ?? 'N/A'}." />
	<meta property="og:type" content="website" />
	<meta property="og:image" content="/og" />
	<meta name="twitter:card" content="summary_large_image" />
</svelte:head>

<div class="min-h-screen bg-bg">
	<header class="border-b border-border">
		<div class="mx-auto flex max-w-5xl items-baseline justify-between px-6 py-5">
			<div>
				<h1 class="text-lg font-semibold tracking-tight text-text">cc-sentiment</h1>
			</div>
			<nav class="flex items-center gap-5 text-sm text-text-muted">
				<a href="/docs" class="hover:text-text transition-colors">Get Started</a>
				<a href="https://github.com/yasyf/cc-sentiment" class="hover:text-text transition-colors" target="_blank" rel="noopener">GitHub</a>
			</nav>
		</div>
	</header>

	<div class="mx-auto max-w-5xl px-6 py-10">
		<div class="mb-10">
			<h2 class="text-3xl font-semibold tracking-tight text-text">
				Does heavy usage correlate with worse sentiment?
			</h2>
			<p class="mt-2 max-w-2xl text-text-muted">
				Crowdsourced from real Claude Code sessions. Transcripts are scored locally, only
				numeric results are uploaded. Inspired by
				<a href="https://github.com/anthropics/claude-code/issues/42796" class="text-accent hover:text-accent-hover transition-colors">claude-code#42796</a>.
			</p>
		</div>

		{#if data.total_records === 0}
			<div class="flex h-64 flex-col items-center justify-center rounded-lg border border-border">
				<p class="text-text-muted">No data yet</p>
				<p class="mt-2 text-sm text-text-dim">
					Run <code class="rounded bg-bg-code px-1.5 py-0.5 font-mono text-xs">cc-sentiment scan --upload</code> to contribute
				</p>
			</div>
		{:else}
			<div class="mb-10 grid grid-cols-2 gap-4 sm:grid-cols-4">
				<div class="rounded-lg border border-border bg-bg-card p-4">
					<p class="text-[11px] font-medium uppercase tracking-widest text-text-dim">Overall</p>
					<p class="mt-2 text-3xl font-semibold tabular-nums {sentimentLabel.color}">{overallAvg.toFixed(1)}</p>
					<p class="mt-0.5 text-xs text-text-muted">{sentimentLabel.text} &middot; {data.total_records.toLocaleString()} records</p>
				</div>

				<div class="rounded-lg border border-border bg-bg-card p-4">
					<p class="text-[11px] font-medium uppercase tracking-widest text-text-dim">Peak Usage</p>
					{#if peakUsageHour}
						<p class="mt-2 text-3xl font-semibold tabular-nums text-text">{peakUsageHour.label}</p>
						<p class="mt-0.5 text-xs text-text-muted">{peakUsageHour.count} records &middot; {peakUsageHour.score.toFixed(1)} avg</p>
					{:else}
						<p class="mt-2 text-3xl font-semibold text-text-dim">--</p>
					{/if}
				</div>

				<div class="rounded-lg border border-border bg-bg-card p-4">
					<p class="text-[11px] font-medium uppercase tracking-widest text-text-dim">Most Frustrated</p>
					{#if worstSentimentHour}
						<p class="mt-2 text-3xl font-semibold tabular-nums text-sentiment-1">{worstSentimentHour.label}</p>
						<p class="mt-0.5 text-xs text-text-muted">{worstSentimentHour.score.toFixed(2)} avg &middot; {worstSentimentHour.count} records</p>
					{:else}
						<p class="mt-2 text-3xl font-semibold text-text-dim">--</p>
					{/if}
				</div>

				<div class="rounded-lg border border-border bg-bg-card p-4">
					<p class="text-[11px] font-medium uppercase tracking-widest text-text-dim">Busiest Day</p>
					{#if busiestDay}
						<p class="mt-2 text-3xl font-semibold tabular-nums text-text">{busiestDay.label}</p>
						<p class="mt-0.5 text-xs text-text-muted">{busiestDay.count} records &middot; {busiestDay.score.toFixed(1)} avg</p>
					{:else}
						<p class="mt-2 text-3xl font-semibold text-text-dim">--</p>
					{/if}
				</div>
			</div>

			<div class="space-y-8">
				<section>
					<h3 class="mb-1 text-sm font-medium text-text-secondary">Sentiment Over Time</h3>
					<div class="rounded-lg border border-border bg-bg-card p-5">
						<SentimentTimeline data={data.timeline} />
					</div>
				</section>

				<section>
					<h3 class="mb-0.5 text-sm font-medium text-text-secondary">Usage Volume vs Sentiment by Hour</h3>
					<p class="mb-2 text-xs text-text-dim">Do the busiest hours also produce the worst scores?</p>
					<div class="rounded-lg border border-border bg-bg-card p-5">
						<HourlyHeatmap data={data.hourly} />
					</div>
				</section>

				<div class="grid grid-cols-1 gap-8 lg:grid-cols-2">
					<section>
						<h3 class="mb-1 text-sm font-medium text-text-secondary">Score Distribution</h3>
						<div class="rounded-lg border border-border bg-bg-card p-5">
							<DistributionHistogram data={data.distribution} />
						</div>
					</section>

					<section>
						<h3 class="mb-1 text-sm font-medium text-text-secondary">Volume & Sentiment by Weekday</h3>
						<div class="rounded-lg border border-border bg-bg-card p-5">
							<WeekdayBar data={data.weekday} />
						</div>
					</section>
				</div>
			</div>
		{/if}
	</div>

	<footer class="border-t border-border">
		<div class="mx-auto max-w-5xl px-6 py-6 text-center text-xs text-text-dim">
			Updated {lastUpdated}
		</div>
	</footer>
</div>
