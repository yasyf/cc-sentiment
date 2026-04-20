import { mode } from 'mode-watcher';

export interface Tooltip {
	backgroundColor: string;
	borderColor: string;
	borderWidth: number;
	titleColor: string;
	bodyColor: string;
	padding: number;
	cornerRadius: number;
	titleFont: { weight: 500 };
	bodyFont: { size: number };
}

export interface ChartPalette {
	GRID: string;
	GRID_ZERO: string;
	TICK: string;
	ACCENT: string;
	ACCENT_LIGHT: string;
	ACCENT_BAR: string;
	ACCENT_BAR_HOVER: string;
	SENTIMENT: Record<number, string>;
	TOOLTIP: Tooltip;
	POINT_BORDER: string;
	ZONE_GOOD: string;
	ZONE_WARN: string;
	ZONE_BAD: string;
	DAY_BOUNDARY: string;
	ANNOTATION_LABEL_BG: string;
	EVENT_REGRESSION: string;
	EVENT_DEPLOY: string;
	DISABLED_BAR: string;
	LEGEND_LABEL: string;
	DAY_PART_TINT: Record<'late' | 'morning' | 'afternoon' | 'evening', string>;
}

const TOOLTIP_BASE = {
	borderWidth: 1,
	padding: 10,
	cornerRadius: 8,
	titleFont: { weight: 500 as const },
	bodyFont: { size: 12 }
};

const LIGHT: ChartPalette = Object.freeze({
	GRID: 'rgba(0, 0, 0, 0.04)',
	GRID_ZERO: 'rgba(0, 0, 0, 0.08)',
	TICK: '#a1a1aa',
	ACCENT: '#6366f1',
	ACCENT_LIGHT: 'rgba(99, 102, 241, 0.12)',
	ACCENT_BAR: 'rgba(99, 102, 241, 0.20)',
	ACCENT_BAR_HOVER: 'rgba(99, 102, 241, 0.35)',
	SENTIMENT: { 1: '#dc2626', 2: '#ea580c', 3: '#ca8a04', 4: '#16a34a', 5: '#0891b2' },
	TOOLTIP: {
		...TOOLTIP_BASE,
		backgroundColor: '#ffffff',
		borderColor: '#e4e4e7',
		titleColor: '#18181b',
		bodyColor: '#71717a'
	},
	POINT_BORDER: '#ffffff',
	ZONE_GOOD: 'rgba(22, 163, 74, 0.04)',
	ZONE_WARN: 'rgba(202, 138, 4, 0.04)',
	ZONE_BAD: 'rgba(220, 38, 38, 0.04)',
	DAY_BOUNDARY: 'rgba(0, 0, 0, 0.18)',
	ANNOTATION_LABEL_BG: 'rgba(255, 255, 255, 0.9)',
	EVENT_REGRESSION: 'rgba(220, 38, 38, 0.3)',
	EVENT_DEPLOY: 'rgba(99, 102, 241, 0.3)',
	DISABLED_BAR: 'rgba(161, 161, 170, 0.3)',
	LEGEND_LABEL: '#71717a',
	DAY_PART_TINT: {
		morning: 'rgba(251, 191, 36, 0.06)',
		afternoon: 'rgba(251, 146, 60, 0.05)',
		evening: 'rgba(167, 139, 250, 0.06)',
		late: 'rgba(99, 102, 241, 0.07)'
	}
});

const DARK: ChartPalette = Object.freeze({
	GRID: 'rgba(255, 255, 255, 0.06)',
	GRID_ZERO: 'rgba(255, 255, 255, 0.12)',
	TICK: '#71717a',
	ACCENT: '#818cf8',
	ACCENT_LIGHT: 'rgba(129, 140, 248, 0.14)',
	ACCENT_BAR: 'rgba(129, 140, 248, 0.22)',
	ACCENT_BAR_HOVER: 'rgba(129, 140, 248, 0.38)',
	SENTIMENT: { 1: '#f87171', 2: '#fb923c', 3: '#fbbf24', 4: '#4ade80', 5: '#22d3ee' },
	TOOLTIP: {
		...TOOLTIP_BASE,
		backgroundColor: '#18181b',
		borderColor: '#27272a',
		titleColor: '#fafafa',
		bodyColor: '#a1a1aa'
	},
	POINT_BORDER: '#18181b',
	ZONE_GOOD: 'rgba(74, 222, 128, 0.09)',
	ZONE_WARN: 'rgba(251, 191, 36, 0.09)',
	ZONE_BAD: 'rgba(248, 113, 113, 0.09)',
	DAY_BOUNDARY: 'rgba(255, 255, 255, 0.12)',
	ANNOTATION_LABEL_BG: 'rgba(24, 24, 27, 0.9)',
	EVENT_REGRESSION: 'rgba(248, 113, 113, 0.45)',
	EVENT_DEPLOY: 'rgba(129, 140, 248, 0.45)',
	DISABLED_BAR: 'rgba(113, 113, 122, 0.35)',
	LEGEND_LABEL: '#a1a1aa',
	DAY_PART_TINT: {
		morning: 'rgba(251, 191, 36, 0.09)',
		afternoon: 'rgba(251, 146, 60, 0.08)',
		evening: 'rgba(167, 139, 250, 0.10)',
		late: 'rgba(129, 140, 248, 0.10)'
	}
});

export const chartTheme: ChartPalette = new Proxy({} as ChartPalette, {
	get(_, key) {
		const palette = mode.current === 'dark' ? DARK : LIGHT;
		return palette[key as keyof ChartPalette];
	}
});

export const SENTIMENT_EMOJI: Record<number, string> = {
	1: '😡', 2: '😕', 3: '😐', 4: '🙂', 5: '😄'
};

export function sentimentColor(v: number): string {
	const scale = chartTheme.SENTIMENT;
	if (v < 2) return scale[1];
	if (v < 2.5) return scale[2];
	if (v < 3.5) return scale[3];
	if (v < 4.5) return scale[4];
	return scale[5];
}

export function sentimentEmoji(v: number): string {
	if (v < 2) return SENTIMENT_EMOJI[1];
	if (v < 2.5) return SENTIMENT_EMOJI[2];
	if (v < 3.5) return SENTIMENT_EMOJI[3];
	if (v < 4.5) return SENTIMENT_EMOJI[4];
	return SENTIMENT_EMOJI[5];
}

export interface PaddedRangeOpts {
	floor?: number;
	ceil?: number;
	padRatio?: number;
	snapInt?: boolean;
	minSpan?: number;
}

export function paddedRange(values: (number | null | undefined)[], opts: PaddedRangeOpts = {}): { min: number; max: number } {
	const nums = values.filter((v): v is number => typeof v === 'number' && Number.isFinite(v));
	if (nums.length === 0) return { min: opts.floor ?? 0, max: opts.ceil ?? 1 };
	const lo = Math.min(...nums);
	const hi = Math.max(...nums);
	let min: number;
	let max: number;
	if (opts.snapInt) {
		min = Math.floor(lo);
		max = Math.ceil(hi);
		if (max - min < (opts.minSpan ?? 0)) {
			const need = opts.minSpan! - (max - min);
			min -= Math.floor(need / 2);
			max += Math.ceil(need / 2);
		}
	} else {
		const span = Math.max(hi - lo, opts.minSpan ?? 1e-6);
		const pad = span * (opts.padRatio ?? 0.15);
		min = lo - pad;
		max = hi + pad;
	}
	if (opts.floor != null) min = Math.max(min, opts.floor);
	if (opts.ceil != null) max = Math.min(max, opts.ceil);
	if (max - min < 1e-6) max = min + 1;
	return { min, max };
}

