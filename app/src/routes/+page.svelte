<script lang="ts">
	import SentimentTimeline from '$lib/charts/SentimentTimeline.svelte';
	import HourlyHeatmap from '$lib/charts/HourlyHeatmap.svelte';
	import WeekdayBar from '$lib/charts/WeekdayBar.svelte';
	import DistributionHistogram from '$lib/charts/DistributionHistogram.svelte';
	import type { PageData } from './$types.js';

	let { data }: { data: PageData } = $props();

	const lastUpdated = $derived(
		new Date(data.last_updated).toLocaleString('en-US', {
			month: 'short',
			day: 'numeric',
			year: 'numeric',
			hour: 'numeric',
			minute: '2-digit'
		})
	);
</script>

<div class="min-h-screen bg-bg px-6 py-8">
	<header class="mx-auto mb-8 max-w-6xl">
		<div class="flex items-baseline justify-between">
			<h1 class="text-2xl font-semibold tracking-tight text-text">cc-sentiment</h1>
			<div class="flex items-center gap-4 text-sm text-text-muted">
				<span>{data.total_records.toLocaleString()} records</span>
				<span class="text-text-dim">|</span>
				<span>Updated {lastUpdated}</span>
			</div>
		</div>
	</header>

	<main class="mx-auto grid max-w-6xl grid-cols-1 gap-6 lg:grid-cols-2">
		<div class="rounded-xl border border-border bg-bg-card p-6">
			<h2 class="mb-4 text-sm font-medium text-text-muted">Sentiment Over Time</h2>
			<SentimentTimeline data={data.timeline} />
		</div>

		<div class="rounded-xl border border-border bg-bg-card p-6">
			<h2 class="mb-4 text-sm font-medium text-text-muted">Sentiment by Hour</h2>
			<HourlyHeatmap data={data.hourly} />
		</div>

		<div class="rounded-xl border border-border bg-bg-card p-6">
			<h2 class="mb-4 text-sm font-medium text-text-muted">Sentiment by Day of Week</h2>
			<WeekdayBar data={data.weekday} />
		</div>

		<div class="rounded-xl border border-border bg-bg-card p-6">
			<h2 class="mb-4 text-sm font-medium text-text-muted">Score Distribution</h2>
			<DistributionHistogram data={data.distribution} />
		</div>
	</main>
</div>
