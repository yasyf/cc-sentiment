<script lang="ts">
	import type { TimelinePoint } from '$lib/types.js';

	const { data }: { data: TimelinePoint[] } = $props();

	const WIDTH = 600;
	const HEIGHT = 240;
	const PADDING = { top: 20, right: 20, bottom: 30, left: 40 };
	const INNER_W = WIDTH - PADDING.left - PADDING.right;
	const INNER_H = HEIGHT - PADDING.top - PADDING.bottom;

	const sorted = $derived(data.toSorted((a, b) => new Date(a.time).getTime() - new Date(b.time).getTime()));

	const xRange = $derived.by(() => {
		if (sorted.length === 0) return { min: Date.now() - 86400000, max: Date.now() };
		const times = sorted.map((d) => new Date(d.time).getTime());
		return { min: Math.min(...times), max: Math.max(...times) };
	});

	function xScale(t: string): number {
		const range = xRange.max - xRange.min || 1;
		return PADDING.left + ((new Date(t).getTime() - xRange.min) / range) * INNER_W;
	}

	function yScale(v: number): number {
		return PADDING.top + INNER_H - ((v - 1) / 4) * INNER_H;
	}

	const linePath = $derived.by(() => {
		if (sorted.length === 0) return '';
		return sorted.map((d, i) => `${i === 0 ? 'M' : 'L'}${xScale(d.time)},${yScale(d.avg_score)}`).join(' ');
	});

	const areaPath = $derived.by(() => {
		if (sorted.length === 0) return '';
		const line = sorted.map((d, i) => `${i === 0 ? 'M' : 'L'}${xScale(d.time)},${yScale(d.avg_score)}`).join(' ');
		const last = sorted[sorted.length - 1];
		const first = sorted[0];
		return `${line} L${xScale(last.time)},${yScale(1)} L${xScale(first.time)},${yScale(1)} Z`;
	});

	const yTicks = [1, 2, 3, 4, 5];

	const xTicks = $derived.by(() => {
		if (sorted.length <= 2) return sorted.map((d) => d.time);
		const step = Math.max(1, Math.floor(sorted.length / 5));
		return sorted.filter((_, i) => i % step === 0).map((d) => d.time);
	});

	function formatDate(t: string): string {
		return new Date(t).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
	}

	let hoveredIndex = $state<number | null>(null);

	function findClosest(clientX: number, rect: DOMRect): number | null {
		if (sorted.length === 0) return null;
		const svgX = ((clientX - rect.left) / rect.width) * WIDTH;
		let closest = 0;
		let minDist = Infinity;
		for (let i = 0; i < sorted.length; i++) {
			const dist = Math.abs(xScale(sorted[i].time) - svgX);
			if (dist < minDist) {
				minDist = dist;
				closest = i;
			}
		}
		return minDist < 30 ? closest : null;
	}
</script>

{#if sorted.length === 0}
	<div class="flex h-72 items-center justify-center text-text-dim">No timeline data yet</div>
{:else}
	<svg
		viewBox="0 0 {WIDTH} {HEIGHT}"
		class="h-72 w-full"
		role="img"
		aria-label="Sentiment over time"
		onmousemove={(e) => {
			const rect = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
			hoveredIndex = findClosest(e.clientX, rect);
		}}
		onmouseleave={() => (hoveredIndex = null)}
	>
		{#each yTicks as tick}
			<line
				x1={PADDING.left}
				y1={yScale(tick)}
				x2={WIDTH - PADDING.right}
				y2={yScale(tick)}
				stroke="var(--color-border)"
				stroke-dasharray="3,3"
			/>
			<text x={PADDING.left - 8} y={yScale(tick) + 4} text-anchor="end" fill="var(--color-text-dim)" font-size="11">
				{tick}
			</text>
		{/each}

		{#each xTicks as tick}
			<text x={xScale(tick)} y={HEIGHT - 6} text-anchor="middle" fill="var(--color-text-dim)" font-size="11">
				{formatDate(tick)}
			</text>
		{/each}

		<defs>
			<linearGradient id="area-grad" x1="0" y1="0" x2="0" y2="1">
				<stop offset="0%" stop-color="var(--color-accent)" stop-opacity="0.25" />
				<stop offset="100%" stop-color="var(--color-accent)" stop-opacity="0.02" />
			</linearGradient>
		</defs>

		<path d={areaPath} fill="url(#area-grad)" />
		<path d={linePath} fill="none" stroke="var(--color-accent)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />

		{#each sorted as point, i}
			<circle
				cx={xScale(point.time)}
				cy={yScale(point.avg_score)}
				r={hoveredIndex === i ? 5 : 3}
				fill={hoveredIndex === i ? 'var(--color-accent-hover)' : 'var(--color-accent)'}
				class="transition-all duration-100"
			/>
		{/each}

		{#if hoveredIndex !== null}
			{@const p = sorted[hoveredIndex]}
			<rect
				x={xScale(p.time) - 55}
				y={yScale(p.avg_score) - 42}
				width="110"
				height="34"
				rx="6"
				fill="var(--color-bg-card)"
				stroke="var(--color-border)"
			/>
			<text x={xScale(p.time)} y={yScale(p.avg_score) - 24} text-anchor="middle" fill="var(--color-text)" font-size="12" font-weight="500">
				{p.avg_score.toFixed(2)} avg ({p.count})
			</text>
			<text x={xScale(p.time)} y={yScale(p.avg_score) - 12} text-anchor="middle" fill="var(--color-text-muted)" font-size="10">
				{formatDate(p.time)}
			</text>
		{/if}
	</svg>
{/if}
