<script lang="ts">
	import SentimentTimeline from '$lib/charts/SentimentTimeline.svelte';
	import HourlyHeatmap from '$lib/charts/HourlyHeatmap.svelte';
	import WeekdayBar from '$lib/charts/WeekdayBar.svelte';
	import DistributionHistogram from '$lib/charts/DistributionHistogram.svelte';
	import ReadEditTimeline from '$lib/charts/ReadEditTimeline.svelte';
	import EditsWithoutReadTimeline from '$lib/charts/EditsWithoutReadTimeline.svelte';
	import ToolCallsPerTurnTimeline from '$lib/charts/ToolCallsPerTurnTimeline.svelte';
	import ModelBreakdown from '$lib/charts/ModelBreakdown.svelte';
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

	const verdict = $derived.by(() => {
		if (overallAvg < 2.0) return { text: 'Claude Code is in trouble.', color: 'text-sentiment-1' };
		if (overallAvg < 2.5) return { text: 'Claude Code is struggling.', color: 'text-sentiment-2' };
		if (overallAvg < 3.5) return { text: 'Claude Code is... okay.', color: 'text-sentiment-3' };
		if (overallAvg < 4.0) return { text: 'Claude Code is doing well.', color: 'text-sentiment-4' };
		return { text: 'Claude Code is thriving.', color: 'text-sentiment-5' };
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

	function fmtHour(h: number): string {
		const ampm = h < 12 ? 'AM' : 'PM';
		const display = h === 0 ? 12 : h > 12 ? h - 12 : h;
		return `${display} ${ampm}`;
	}

	function utcToPT(h: number): string {
		const pt = (h - 7 + 24) % 24;
		return fmtHour(pt);
	}

	const worstSentimentHour = $derived.by(() => {
		const significant = data.hourly.filter((h) => h.count >= 5);
		if (significant.length === 0) return null;
		const worst = significant.toSorted((a, b) => a.avg_score - b.avg_score)[0];
		return { label: fmtHour(worst.hour), ptLabel: utcToPT(worst.hour), score: worst.avg_score, count: worst.count };
	});

	const frustratedPct = $derived.by(() => {
		const total = data.distribution.reduce((s, d) => s + d.count, 0);
		if (total === 0) return 0;
		const frustrated = data.distribution.filter((d) => d.score <= 2).reduce((s, d) => s + d.count, 0);
		return (frustrated / total) * 100;
	});

	function readEditColor(v: number | null): string {
		if (v == null) return 'text-text-dim';
		if (v >= 4) return 'text-sentiment-4';
		if (v >= 2) return 'text-sentiment-3';
		return 'text-sentiment-1';
	}

	let showAdvanced = $state(false);
</script>

<svelte:head>
	<title>cc-sentiment — {verdict.text}</title>
	<meta name="description" content="{verdict.text} Sentiment is {overallAvg.toFixed(1)}/5 across {data.total_sessions.toLocaleString()} sessions. Live data scored on-device." />
	<meta property="og:title" content="cc-sentiment — {verdict.text}" />
	<meta property="og:description" content="{overallAvg.toFixed(1)}/5 across {data.total_sessions.toLocaleString()} sessions — {trendDescription} vs last week." />
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
				{overallAvg.toFixed(1)}/5 across {data.total_sessions.toLocaleString()} sessions this week — {trendDescription}.
			</p>
			<p class="mt-1 max-w-2xl text-sm text-text-dim">
				An open experiment: does Claude Code sentiment vary with time of day, day of week, or model?
				Anyone can contribute — scores are generated on your Mac and only the numbers leave.
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
					<p class="text-[11px] font-medium uppercase tracking-widest text-text-dim">Sentiment</p>
					<p class="mt-2 text-3xl font-semibold tabular-nums {sentimentLabel.color}">{overallAvg.toFixed(1)}<span class="text-base text-text-dim">/5</span></p>
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
					<p class="text-[11px] font-medium uppercase tracking-widest text-text-dim">Read:Edit Ratio</p>
					{#if data.avg_read_edit_ratio != null}
						<p class="mt-2 text-3xl font-semibold tabular-nums {readEditColor(data.avg_read_edit_ratio)}">{data.avg_read_edit_ratio.toFixed(1)}</p>
						<p class="mt-0.5 text-xs text-text-muted">reads per edit</p>
					{:else}
						<p class="mt-2 text-3xl font-semibold text-text-dim">--</p>
						<p class="mt-0.5 text-xs text-text-dim">no data yet</p>
					{/if}
				</div>

				<div class="rounded-lg border border-border bg-bg-card p-4">
					<p class="text-[11px] font-medium uppercase tracking-widest text-text-dim">Peak frustration</p>
					{#if worstSentimentHour}
						<p class="mt-2 text-3xl font-semibold tabular-nums text-sentiment-1">{worstSentimentHour.ptLabel}</p>
						<p class="mt-0.5 text-xs text-text-muted">{worstSentimentHour.score.toFixed(2)} avg · {worstSentimentHour.label} UTC</p>
					{:else}
						<p class="mt-2 text-3xl font-semibold text-text-dim">--</p>
					{/if}
				</div>

				<div class="rounded-lg border border-border bg-bg-card p-4">
					<p class="text-[11px] font-medium uppercase tracking-widest text-text-dim">Community</p>
					<p class="mt-2 text-3xl font-semibold tabular-nums text-text">{data.total_sessions.toLocaleString()}</p>
					<p class="mt-0.5 text-xs text-text-muted">
						{data.total_contributors} contributor{data.total_contributors === 1 ? '' : 's'}
						{#if sessionsDelta != null && Math.abs(sessionsDelta) >= 5}
							<span class={sessionsDelta > 0 ? 'text-sentiment-4' : 'text-sentiment-1'}>
								{sessionsDelta > 0 ? '+' : ''}{sessionsDelta.toFixed(0)}%
							</span>
						{/if}
					</p>
				</div>
			</div>

			<div class="space-y-8">
				<section>
					<h3 class="mb-0.5 text-sm font-medium text-text-secondary">The trend</h3>
					<p class="mb-2 text-xs text-text-dim">
						Average sentiment and session volume over time.
					</p>
					<div class="rounded-lg border border-border bg-bg-card p-5">
						<SentimentTimeline data={data.timeline} />
					</div>
				</section>

				<section>
					<h3 class="mb-0.5 text-sm font-medium text-text-secondary">By hour of day</h3>
					<p class="mb-2 text-xs text-text-dim">
						Average sentiment and session volume by hour (UTC).
					</p>
					<div class="rounded-lg border border-border bg-bg-card p-5">
						<HourlyHeatmap data={data.hourly} />
					</div>
				</section>

				<div class="grid grid-cols-1 gap-8 lg:grid-cols-2">
					<section>
						<h3 class="mb-0.5 text-sm font-medium text-text-secondary">How sessions feel</h3>
						<p class="mb-2 text-xs text-text-dim">
							{frustratedPct.toFixed(0)}% of sessions score frustrated or annoyed.
						</p>
						<div class="rounded-lg border border-border bg-bg-card p-5">
							<DistributionHistogram data={data.distribution} />
						</div>
					</section>

					<section>
						<h3 class="mb-0.5 text-sm font-medium text-text-secondary">Day of week</h3>
						<p class="mb-2 text-xs text-text-dim">
							Session volume and sentiment by weekday.
						</p>
						<div class="rounded-lg border border-border bg-bg-card p-5">
							<WeekdayBar data={data.weekday} />
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
										How many files Claude reads before making an edit. Higher is better — green (&gt;4) means thorough research, red (&lt;2) means lazy editing.
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
										Share of edits where Claude hadn't yet read the file in this session. Lower means more research before changes.
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
										Average number of tools Claude invokes between user messages.
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
										Average sentiment by Claude model, across scored sessions.
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
