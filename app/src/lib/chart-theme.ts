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

export function sentimentColor(v: number): string {
	if (v < 2) return SENTIMENT[1];
	if (v < 2.5) return SENTIMENT[2];
	if (v < 3.5) return SENTIMENT[3];
	if (v < 4.5) return SENTIMENT[4];
	return SENTIMENT[5];
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
