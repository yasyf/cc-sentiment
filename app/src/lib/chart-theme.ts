export const GRID = 'rgba(0, 0, 0, 0.04)';
export const GRID_ZERO = 'rgba(0, 0, 0, 0.08)';
export const TICK = '#a1a1aa';
export const ACCENT = '#6366f1';
export const ACCENT_LIGHT = 'rgba(99, 102, 241, 0.12)';
export const ACCENT_BAR = 'rgba(99, 102, 241, 0.20)';
export const ACCENT_BAR_HOVER = 'rgba(99, 102, 241, 0.35)';

export const SENTIMENT: Record<number, string> = {
	1: '#dc2626', 2: '#ea580c', 3: '#ca8a04', 4: '#16a34a', 5: '#0891b2'
};

export const SENTIMENT_EMOJI: Record<number, string> = {
	1: '😡', 2: '😕', 3: '😐', 4: '🙂', 5: '😄'
};

export function sentimentColor(v: number): string {
	if (v < 2) return SENTIMENT[1];
	if (v < 2.5) return SENTIMENT[2];
	if (v < 3.5) return SENTIMENT[3];
	if (v < 4.5) return SENTIMENT[4];
	return SENTIMENT[5];
}

export function sentimentEmoji(v: number): string {
	if (v < 2) return SENTIMENT_EMOJI[1];
	if (v < 2.5) return SENTIMENT_EMOJI[2];
	if (v < 3.5) return SENTIMENT_EMOJI[3];
	if (v < 4.5) return SENTIMENT_EMOJI[4];
	return SENTIMENT_EMOJI[5];
}

export const TOOLTIP = {
	backgroundColor: '#ffffff',
	borderColor: '#e4e4e7',
	borderWidth: 1,
	titleColor: '#18181b',
	bodyColor: '#71717a',
	padding: 10,
	cornerRadius: 8,
	titleFont: { weight: 500 as const },
	bodyFont: { size: 12 }
};

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
