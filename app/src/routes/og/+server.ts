import { ImageResponse } from '@vercel/og';
import { fetchData } from '$lib/api.js';
import type { RequestHandler } from './$types.js';

export const GET: RequestHandler = async ({ fetch }) => {
	const data = await fetchData(fetch);

	const total = data.distribution.reduce((s, d) => s + d.count, 0);
	const avg = total > 0
		? (data.distribution.reduce((s, d) => s + d.score * d.count, 0) / total).toFixed(1)
		: '?';

	const label =
		Number(avg) < 1.5 ? 'Frustrated' :
		Number(avg) < 2.5 ? 'Annoyed' :
		Number(avg) < 3.5 ? 'Neutral' :
		Number(avg) < 4.5 ? 'Satisfied' : 'Delighted';

	const significant = data.hourly.filter((h) => h.count >= 5);
	const worst = significant.length > 0
		? significant.toSorted((a, b) => a.avg_score - b.avg_score)[0]
		: null;

	function fmtHour(h: number): string {
		if (h === 0) return '12 AM';
		if (h < 12) return `${h} AM`;
		if (h === 12) return '12 PM';
		return `${h - 12} PM`;
	}

	const html = {
		type: 'div',
		props: {
			style: {
				display: 'flex',
				flexDirection: 'column',
				justifyContent: 'center',
				width: '100%',
				height: '100%',
				padding: '60px 80px',
				backgroundColor: '#fafafa',
				fontFamily: 'Inter, system-ui, sans-serif',
			},
			children: [
				{
					type: 'div',
					props: {
						style: { display: 'flex', alignItems: 'baseline', gap: '12px' },
						children: [
							{
								type: 'span',
								props: {
									style: { fontSize: '32px', fontWeight: 600, color: '#18181b' },
									children: 'cc-sentiment',
								},
							},
							{
								type: 'span',
								props: {
									style: { fontSize: '18px', color: '#a1a1aa' },
									children: `${data.total_sessions.toLocaleString()} sessions from ${data.total_contributors} contributors`,
								},
							},
						],
					},
				},
				{
					type: 'div',
					props: {
						style: { display: 'flex', gap: '48px', marginTop: '40px' },
						children: [
							{
								type: 'div',
								props: {
									style: { display: 'flex', flexDirection: 'column' },
									children: [
										{
											type: 'span',
											props: { style: { fontSize: '14px', color: '#a1a1aa', textTransform: 'uppercase', letterSpacing: '0.05em' }, children: 'Sentiment' },
										},
										{
											type: 'span',
											props: { style: { fontSize: '64px', fontWeight: 600, color: '#6366f1', lineHeight: 1.1 }, children: avg },
										},
										{
											type: 'span',
											props: { style: { fontSize: '16px', color: '#71717a' }, children: label },
										},
									],
								},
							},
							...(worst ? [{
								type: 'div',
								props: {
									style: { display: 'flex', flexDirection: 'column' },
									children: [
										{
											type: 'span',
											props: { style: { fontSize: '14px', color: '#a1a1aa', textTransform: 'uppercase', letterSpacing: '0.05em' }, children: 'Worst Hour' },
										},
										{
											type: 'span',
											props: { style: { fontSize: '64px', fontWeight: 600, color: '#dc2626', lineHeight: 1.1 }, children: fmtHour(worst.hour) },
										},
										{
											type: 'span',
											props: { style: { fontSize: '16px', color: '#71717a' }, children: `${worst.avg_score.toFixed(2)} avg` },
										},
									],
								},
							}] : []),
						],
					},
				},
				{
					type: 'div',
					props: {
						style: { marginTop: '40px', fontSize: '16px', color: '#71717a', maxWidth: '600px' },
						children: 'How frustrated are developers with Claude Code? Live sentiment from real sessions, scored on-device.',
					},
				},
			],
		},
	};

	return new ImageResponse(html, { width: 1200, height: 630 });
};
