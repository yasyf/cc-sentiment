export function describeTrend(current: number, previous: number): string {
	if (previous === 0) return 'holding steady';
	const pct = ((current - previous) / previous) * 100;
	const abs = Math.abs(pct);
	if (abs < 1) return 'holding steady';
	return `${pct > 0 ? 'up' : 'down'} ${abs.toFixed(0)}% from last week`;
}
