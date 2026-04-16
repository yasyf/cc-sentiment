<script lang="ts">
	import { DateTime } from 'luxon';
	import SentimentTimeline from '$lib/charts/SentimentTimeline.svelte';
	import HourlyHeatmap from '$lib/charts/HourlyHeatmap.svelte';
	import WeekdayBar from '$lib/charts/WeekdayBar.svelte';
	import DistributionHistogram from '$lib/charts/DistributionHistogram.svelte';
	import ReadEditTimeline from '$lib/charts/ReadEditTimeline.svelte';
	import EditsWithoutReadTimeline from '$lib/charts/EditsWithoutReadTimeline.svelte';
	import ToolCallsPerTurnTimeline from '$lib/charts/ToolCallsPerTurnTimeline.svelte';
	import ModelBreakdown from '$lib/charts/ModelBreakdown.svelte';
	import { sentimentEmoji } from '$lib/chart-theme.js';
	import type { PageProps } from './$types.js';

	const DISPLAY_TZ = 'America/Los_Angeles';

	let { data }: PageProps = $props();

	const lastUpdated = $derived(
		new Date(data.last_updated).toLocaleString('en-US', {
			timeZone: 'America/Los_Angeles',
			month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
			timeZoneName: 'short'
		})
	);

	const overallAvg = $derived.by(() => {
		const total = data.distribution.reduce((s, d) => s + d.count, 0);
		if (total === 0) return 0;
		return data.distribution.reduce((s, d) => s + d.score * d.count, 0) / total;
	});

	const verdict = $derived.by(() => {
		if (overallAvg < 2.0) return { text: 'Developers are frustrated.', color: 'text-sentiment-1' };
		if (overallAvg < 2.5) return { text: 'Developers are struggling.', color: 'text-sentiment-2' };
		if (overallAvg < 3.5) return { text: 'Developers are getting by.', color: 'text-sentiment-3' };
		if (overallAvg < 4.0) return { text: 'Developers are happy.', color: 'text-sentiment-4' };
		return { text: 'Developers are thriving.', color: 'text-sentiment-5' };
	});

	const sentimentLabel = $derived.by(() => {
		if (overallAvg < 1.5) return { text: 'Frustrated', color: 'text-sentiment-1' };
		if (overallAvg < 2.5) return { text: 'Annoyed', color: 'text-sentiment-2' };
		if (overallAvg < 3.5) return { text: 'Neutral', color: 'text-sentiment-3' };
		if (overallAvg < 4.5) return { text: 'Satisfied', color: 'text-sentiment-4' };
		return { text: 'Delighted', color: 'text-sentiment-5' };
	});

	const sentimentDelta = $derived(data.trend.sentiment_current - data.trend.sentiment_previous);
	const sessionsDelta = $derived.by(() => {
		if (data.trend.sessions_previous === 0) return null;
		return ((data.trend.sessions_current - data.trend.sessions_previous) / data.trend.sessions_previous) * 100;
	});

	const trendDescription = $derived.by(() => {
		const abs = Math.abs(sentimentDelta);
		if (abs < 0.1) return 'holding steady';
		return sentimentDelta > 0 ? `up ${abs.toFixed(1)}` : `down ${abs.toFixed(1)}`;
	});

	const sessionsTrendDescription = $derived.by(() => {
		if (sessionsDelta == null) return null;
		const abs = Math.abs(sessionsDelta);
		if (abs < 5) return 'about the same as last week';
		return `${sessionsDelta > 0 ? 'up' : 'down'} ${abs.toFixed(0)}% vs last week`;
	});

	function fmtHour(h: number): string {
		const ampm = h < 12 ? 'AM' : 'PM';
		const display = h === 0 ? 12 : h > 12 ? h - 12 : h;
		return `${display} ${ampm}`;
	}

	function bucketTimelineBy(keyFn: (dt: DateTime) => number, size: number) {
		const sums = new Array(size).fill(null).map(() => ({ score: 0, count: 0 }));
		for (const point of data.timeline) {
			const dt = DateTime.fromISO(point.time, { zone: DISPLAY_TZ });
			if (!dt.isValid) continue;
			const bucket = sums[keyFn(dt)];
			bucket.score += point.avg_score * point.count;
			bucket.count += point.count;
		}
		return sums.map((s, i) => ({
			key: i,
			avg_score: s.count > 0 ? s.score / s.count : 0,
			count: s.count
		}));
	}

	const hourlyData = $derived.by(() =>
		bucketTimelineBy((dt) => dt.hour, 24).map(({ key, avg_score, count }) => ({
			hour: key, avg_score, count
		}))
	);

	const weekdayData = $derived.by(() =>
		bucketTimelineBy((dt) => dt.weekday % 7, 7).map(({ key, avg_score, count }) => ({
			dow: key, avg_score, count
		}))
	);

	const worstSentimentHour = $derived.by(() => {
		const significant = hourlyData.filter((h) => h.count >= 5);
		if (significant.length === 0) return null;
		const worst = significant.toSorted((a, b) => a.avg_score - b.avg_score)[0];
		return { ptLabel: fmtHour(worst.hour), score: worst.avg_score, count: worst.count };
	});

	const frustratedPct = $derived.by(() => {
		const total = data.distribution.reduce((s, d) => s + d.count, 0);
		if (total === 0) return 0;
		const frustrated = data.distribution.filter((d) => d.score <= 2).reduce((s, d) => s + d.count, 0);
		return (frustrated / total) * 100;
	});

	let showAdvanced = $state(false);
</script>

<svelte:head>
	<title>cc-sentiment</title>
	<meta name="description" content="{verdict.text} Average sentiment is {overallAvg.toFixed(1)}/5 across {data.total_sessions.toLocaleString()} sessions. Live data scored on-device." />
	<meta property="og:title" content="cc-sentiment" />
	<meta property="og:description" content="{verdict.text} {overallAvg.toFixed(1)}/5 across {data.total_sessions.toLocaleString()} sessions. {trendDescription} vs last week." />
	<meta property="og:image" content="/og" />
	<meta name="twitter:title" content="cc-sentiment" />
	<meta name="twitter:description" content="{verdict.text} {overallAvg.toFixed(1)}/5 across {data.total_sessions.toLocaleString()} sessions. {trendDescription} vs last week." />
</svelte:head>

<div class="min-h-screen bg-bg">
	<header class="border-b border-border">
		<div class="mx-auto flex max-w-5xl items-baseline justify-between px-6 py-5">
			<div>
				<h1 class="text-lg font-semibold tracking-tight text-text">cc-sentiment</h1>
			</div>
			<nav class="flex items-center gap-5 text-sm text-text-muted">
				<a href="/docs" class="hover:text-text transition-colors">Contribute</a>
				<a href="https://github.com/yasyf/cc-sentiment" class="hover:text-text transition-colors" target="_blank" rel="noopener">GitHub</a>
			</nav>
		</div>
	</header>

	<div class="mx-auto max-w-5xl px-6 py-10">
		<div class="mb-10">
			<h2 class="text-3xl font-semibold tracking-tight {verdict.color}">
				{verdict.text}
			</h2>
			<p class="mt-2 text-lg text-text-muted">
				{overallAvg.toFixed(1)}/5 across {data.trend.sessions_current.toLocaleString()} sessions this week. {trendDescription}.
			</p>
			<p class="mt-1 max-w-2xl text-sm text-text-dim">
				An open experiment: does developer sentiment with Claude Code vary by time of day, day of week, or model?
				Anyone can contribute. Scoring runs on your Mac, only the numbers leave.
			</p>
		</div>

		{#if data.total_records === 0}
			<div class="flex h-64 flex-col items-center justify-center rounded-lg border border-border">
				<p class="text-text-muted">No data yet</p>
				<p class="mt-2 text-sm text-text-dim">
					<a href="/docs" class="text-accent hover:text-accent-hover">Contribute your data</a> to get started
				</p>
			</div>
		{:else}
			<div class="mb-10 grid grid-cols-2 gap-4 sm:grid-cols-4">
				<div class="rounded-lg border border-border bg-bg-card p-4">
					<p class="text-[11px] font-medium uppercase tracking-widest text-text-dim">Peak frustration</p>
					{#if worstSentimentHour}
						<p class="mt-2 text-3xl font-semibold tabular-nums text-sentiment-1">{worstSentimentHour.ptLabel}</p>
						<p class="mt-0.5 text-xs text-text-muted">{sentimentEmoji(worstSentimentHour.score)} {worstSentimentHour.score.toFixed(2)} avg</p>
					{:else}
						<p class="mt-2 text-3xl font-semibold text-text-dim">--</p>
						<p class="mt-0.5 text-xs text-text-dim">not enough data yet</p>
					{/if}
				</div>

				<div class="rounded-lg border border-border bg-bg-card p-4">
					<p class="text-[11px] font-medium uppercase tracking-widest text-text-dim">Sentiment this week</p>
					<p class="mt-2 text-3xl font-semibold tabular-nums {sentimentLabel.color}">
						{overallAvg.toFixed(1)} <span class="text-2xl">{sentimentEmoji(overallAvg)}</span>
					</p>
					<p class="mt-0.5 text-xs text-text-muted">
						{sentimentLabel.text}
						{#if Math.abs(sentimentDelta) >= 0.1}
							<span class={sentimentDelta > 0 ? 'text-sentiment-4' : 'text-sentiment-1'}>
								{sentimentDelta > 0 ? '+' : ''}{sentimentDelta.toFixed(1)}
							</span>
						{/if}
					</p>
				</div>

				<div class="rounded-lg border border-border bg-bg-card p-4">
					<p class="text-[11px] font-medium uppercase tracking-widest text-text-dim">Sessions this week</p>
					<p class="mt-2 text-3xl font-semibold tabular-nums text-text">{data.trend.sessions_current.toLocaleString()}</p>
					<p class="mt-0.5 text-xs text-text-muted">
						{#if sessionsTrendDescription}
							<span class={sessionsDelta != null && sessionsDelta < -5 ? 'text-sentiment-1' : sessionsDelta != null && sessionsDelta > 5 ? 'text-sentiment-4' : ''}>
								{sessionsTrendDescription}
							</span>
						{:else}
							first week with data
						{/if}
					</p>
				</div>

				<div class="rounded-lg border border-border bg-bg-card p-4">
					<p class="text-[11px] font-medium uppercase tracking-widest text-text-dim">Contributors</p>
					<p class="mt-2 text-3xl font-semibold tabular-nums text-text">{data.total_contributors}</p>
					<p class="mt-0.5 text-xs text-text-muted">people who shared data</p>
				</div>
			</div>

			<div class="space-y-8">
				<section>
					<h3 class="mb-0.5 text-sm font-medium text-text-secondary">By hour of day</h3>
					<p class="mb-2 text-xs text-text-dim">
						Average sentiment and session volume by hour (PT, last 30 days).
					</p>
					<div class="rounded-lg border border-border bg-bg-card p-5">
						<HourlyHeatmap data={hourlyData} />
					</div>
				</section>

				<section>
					<h3 class="mb-0.5 text-sm font-medium text-text-secondary">The trend</h3>
					<p class="mb-2 text-xs text-text-dim">
						Daily sentiment and session volume (last 30 days).
					</p>
					<div class="rounded-lg border border-border bg-bg-card p-5">
						<SentimentTimeline data={data.timeline} />
					</div>
				</section>

				<div class="grid grid-cols-1 gap-8 lg:grid-cols-2">
					<section>
						<h3 class="mb-0.5 text-sm font-medium text-text-secondary">How sessions feel</h3>
						<p class="mb-2 text-xs text-text-dim">
							Distribution of sentiment this week. {frustratedPct.toFixed(0)}% scored frustrated or annoyed.
						</p>
						<div class="rounded-lg border border-border bg-bg-card p-5">
							<DistributionHistogram data={data.distribution} />
						</div>
					</section>

					<section>
						<h3 class="mb-0.5 text-sm font-medium text-text-secondary">Day of week</h3>
						<p class="mb-2 text-xs text-text-dim">
							Session volume and sentiment by weekday (last 30 days).
						</p>
						<div class="rounded-lg border border-border bg-bg-card p-5">
							<WeekdayBar data={weekdayData} />
						</div>
					</section>
				</div>

				<div class="border-t border-border pt-6">
					<button
						onclick={() => showAdvanced = !showAdvanced}
						class="flex items-center gap-2 text-sm font-medium text-text-muted hover:text-text transition-colors"
					>
						<span class="inline-block transition-transform {showAdvanced ? 'rotate-90' : ''}">&rsaquo;</span>
						Under the hood
					</button>

					{#if showAdvanced}
						<div class="mt-6 space-y-8">
							{#if data.timeline.some((d) => d.avg_read_edit_ratio != null)}
								<section>
									<h3 class="mb-0.5 text-sm font-medium text-text-secondary">Read:Edit ratio over time</h3>
									<p class="mb-2 text-xs text-text-dim">
										How many files Claude reads before making an edit (last 30 days). Higher is better. Green (&gt;4) means thorough research, red (&lt;2) means lazy editing.
									</p>
									<div class="rounded-lg border border-border bg-bg-card p-5">
										<ReadEditTimeline data={data.timeline} />
									</div>
								</section>
							{/if}

							{#if data.timeline.some((d) => d.avg_edits_without_prior_read_ratio != null)}
								<section>
									<h3 class="mb-0.5 text-sm font-medium text-text-secondary">Edits without prior read</h3>
									<p class="mb-2 text-xs text-text-dim">
										Share of edits where Claude hadn't yet read the file in this session (last 30 days). Lower means more research before changes.
									</p>
									<div class="rounded-lg border border-border bg-bg-card p-5">
										<EditsWithoutReadTimeline data={data.timeline} />
									</div>
								</section>
							{/if}

							{#if data.timeline.some((d) => d.avg_tool_calls_per_turn != null)}
								<section>
									<h3 class="mb-0.5 text-sm font-medium text-text-secondary">Tool calls per turn</h3>
									<p class="mb-2 text-xs text-text-dim">
										Average number of tools Claude invokes between user messages (last 30 days).
									</p>
									<div class="rounded-lg border border-border bg-bg-card p-5">
										<ToolCallsPerTurnTimeline data={data.timeline} />
									</div>
								</section>
							{/if}

							{#if data.model_breakdown.length > 0}
								<section>
									<h3 class="mb-0.5 text-sm font-medium text-text-secondary">By model</h3>
									<p class="mb-2 text-xs text-text-dim">
										Average sentiment by Claude model (last 30 days).
									</p>
									<div class="rounded-lg border border-border bg-bg-card p-5">
										<ModelBreakdown data={data.model_breakdown} />
									</div>
								</section>
							{/if}

							<div class="grid grid-cols-2 gap-4 text-center sm:grid-cols-4">
								<div class="rounded-lg border border-border bg-bg-card p-4">
									<p class="text-2xl font-semibold tabular-nums text-text">
										{data.avg_read_edit_ratio != null ? data.avg_read_edit_ratio.toFixed(1) : '—'}
									</p>
									<p class="mt-1 text-xs text-text-dim">avg read:edit ratio</p>
								</div>
								<div class="rounded-lg border border-border bg-bg-card p-4">
									<p class="text-2xl font-semibold tabular-nums text-text">
										{data.avg_write_edit_ratio != null ? `${(data.avg_write_edit_ratio * 100).toFixed(0)}%` : '—'}
									</p>
									<p class="mt-1 text-xs text-text-dim">avg write share of writes+edits</p>
								</div>
								<div class="rounded-lg border border-border bg-bg-card p-4">
									<p class="text-2xl font-semibold tabular-nums text-text">
										{data.avg_subagent_count != null ? data.avg_subagent_count.toFixed(2) : '—'}
									</p>
									<p class="mt-1 text-xs text-text-dim">avg subagents per bucket</p>
								</div>
								<div class="rounded-lg border border-border bg-bg-card p-4">
									<p class="text-2xl font-semibold tabular-nums text-text">
										{data.avg_tool_calls_per_turn != null ? data.avg_tool_calls_per_turn.toFixed(1) : '—'}
									</p>
									<p class="mt-1 text-xs text-text-dim">avg tool calls per turn</p>
								</div>
							</div>

							<div class="grid grid-cols-3 gap-4 text-center">
								<div class="rounded-lg border border-border bg-bg-card p-4">
									<p class="text-2xl font-semibold tabular-nums text-text">{data.total_records.toLocaleString()}</p>
									<p class="mt-1 text-xs text-text-dim">total records</p>
								</div>
								<div class="rounded-lg border border-border bg-bg-card p-4">
									<p class="text-2xl font-semibold tabular-nums text-text">{data.total_contributors}</p>
									<p class="mt-1 text-xs text-text-dim">contributors</p>
								</div>
								<div class="rounded-lg border border-border bg-bg-card p-4">
									<p class="text-2xl font-semibold tabular-nums text-text">{(data.total_records / Math.max(data.total_sessions, 1)).toFixed(1)}</p>
									<p class="mt-1 text-xs text-text-dim">records / session</p>
								</div>
							</div>
						</div>
					{/if}
				</div>
			</div>
		{/if}
	</div>

	<footer class="border-t border-border">
		<div class="mx-auto flex max-w-5xl items-center justify-between px-6 py-6 text-xs text-text-dim">
			<span>Updated {lastUpdated}</span>
			<a href="/docs" class="text-accent hover:text-accent-hover transition-colors">
				Add yours in 30 seconds →
			</a>
		</div>
	</footer>
</div>
