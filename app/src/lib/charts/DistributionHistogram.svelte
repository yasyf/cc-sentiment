<script lang="ts">
	import type { DistributionPoint } from '../types.js';

	const { data }: { data: DistributionPoint[] } = $props();

	const WIDTH = 600;
	const HEIGHT = 240;
	const PADDING = { top: 20, right: 20, bottom: 30, left: 40 };
	const INNER_W = WIDTH - PADDING.left - PADDING.right;
	const INNER_H = HEIGHT - PADDING.top - PADDING.bottom;
	const BAR_GAP = 12;

	const SCORE_COLORS: Record<number, string> = {
		1: 'var(--color-sentiment-1)',
		2: 'var(--color-sentiment-2)',
		3: 'var(--color-sentiment-3)',
		4: 'var(--color-sentiment-4)',
		5: 'var(--color-sentiment-5)'
	};

	const SCORE_LABELS: Record<number, string> = {
		1: 'Frustrated',
		2: 'Annoyed',
		3: 'Neutral',
		4: 'Satisfied',
		5: 'Delighted'
	};

	const sorted = $derived(data.toSorted((a, b) => a.score - b.score));
	const maxCount = $derived(Math.max(1, ...sorted.map((d) => d.count)));
	const totalCount = $derived(sorted.reduce((sum, d) => sum + d.count, 0));

	function barWidth(): number {
		return (INNER_W - BAR_GAP * 4) / 5;
	}

	function barX(i: number): number {
		return PADDING.left + i * (barWidth() + BAR_GAP);
	}

	function barHeight(count: number): number {
		return (count / maxCount) * INNER_H;
	}

	let hoveredIndex = $state<number | null>(null);
</script>

{#if sorted.length === 0}
	<div class="flex h-72 items-center justify-center text-text-dim">No distribution data yet</div>
{:else}
	<svg
		viewBox="0 0 {WIDTH} {HEIGHT}"
		class="h-72 w-full"
		role="img"
		aria-label="Score distribution"
	>
		{#each sorted as point, i}
			{@const bw = barWidth()}
			{@const bh = barHeight(point.count)}
			{@const x = barX(i)}
			{@const y = PADDING.top + INNER_H - bh}
			{@const pct = totalCount > 0 ? ((point.count / totalCount) * 100).toFixed(0) : '0'}

			<!-- svelte-ignore a11y_no_static_element_interactions -->
			<rect
				{x}
				{y}
				width={bw}
				height={bh}
				rx="4"
				fill={SCORE_COLORS[point.score] ?? 'var(--color-accent)'}
				opacity={hoveredIndex === null || hoveredIndex === i ? 1 : 0.4}
				class="transition-opacity duration-150"
				onmouseenter={() => (hoveredIndex = i)}
				onmouseleave={() => (hoveredIndex = null)}
			/>

			<text
				x={x + bw / 2}
				y={y - 6}
				text-anchor="middle"
				fill="var(--color-text-muted)"
				font-size="11"
				font-weight="500"
			>
				{pct}%
			</text>

			<text
				x={x + bw / 2}
				y={HEIGHT - 6}
				text-anchor="middle"
				fill="var(--color-text-dim)"
				font-size="11"
			>
				{point.score}
			</text>
		{/each}

		{#if hoveredIndex !== null}
			{@const p = sorted[hoveredIndex]}
			{@const x = barX(hoveredIndex) + barWidth() / 2}
			<rect
				x={x - 55}
				y="2"
				width="110"
				height="18"
				rx="4"
				fill="var(--color-bg-card)"
				stroke="var(--color-border)"
			/>
			<text x={x} y="14" text-anchor="middle" fill="var(--color-text)" font-size="11">
				{SCORE_LABELS[p.score]}: {p.count}
			</text>
		{/if}
	</svg>
{/if}
