import { ImageResponse } from '@vercel/og';
import { fetchData } from '$lib/api.js';
import type { RequestHandler } from './$types.js';

export const GET: RequestHandler = async ({ fetch }) => {
	const data = await fetchData(fetch);

	const total = data.distribution.reduce((s, d) => s + d.count, 0);
	const avg = total > 0
		? (data.distribution.reduce((s, d) => s + d.score * d.count, 0) / total).toFixed(1)
		: '?';

	const numAvg = Number(avg);

	const verdict =
		numAvg < 2.0 ? 'Claude Code is in trouble.' :
		numAvg < 2.5 ? 'Claude Code is struggling.' :
		numAvg < 3.5 ? 'Claude Code is... okay.' :
		numAvg < 4.0 ? 'Claude Code is doing well.' :
		'Claude Code is thriving.';

	const verdictColor =
		numAvg < 2.0 ? '#dc2626' :
		numAvg < 2.5 ? '#ea580c' :
		numAvg < 3.5 ? '#ca8a04' :
		numAvg < 4.0 ? '#16a34a' :
		'#059669';

	const sentimentDelta = data.trend.sentiment_current - data.trend.sentiment_previous;
	const trendAbs = Math.abs(sentimentDelta);
	const trendText = trendAbs < 0.1
		? 'holding steady'
		: sentimentDelta > 0 ? `up ${trendAbs.toFixed(1)} vs last week` : `down ${trendAbs.toFixed(1)} vs last week`;

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
									style: { fontSize: '28px', fontWeight: 600, color: '#18181b' },
									children: 'cc-sentiment',
								},
							},
						],
					},
				},
				{
					type: 'div',
					props: {
						style: { marginTop: '32px', fontSize: '52px', fontWeight: 700, color: verdictColor, lineHeight: 1.1 },
						children: verdict,
					},
				},
				{
					type: 'div',
					props: {
						style: { display: 'flex', gap: '40px', marginTop: '36px' },
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
											props: { style: { fontSize: '56px', fontWeight: 600, color: verdictColor, lineHeight: 1.1 }, children: `${avg}/5` },
										},
										{
											type: 'span',
											props: { style: { fontSize: '16px', color: '#71717a' }, children: trendText },
										},
									],
								},
							},
							{
								type: 'div',
								props: {
									style: { display: 'flex', flexDirection: 'column' },
									children: [
										{
											type: 'span',
											props: { style: { fontSize: '14px', color: '#a1a1aa', textTransform: 'uppercase', letterSpacing: '0.05em' }, children: 'This week' },
										},
										{
											type: 'span',
											props: { style: { fontSize: '56px', fontWeight: 600, color: '#18181b', lineHeight: 1.1 }, children: data.total_sessions.toLocaleString() },
										},
										{
											type: 'span',
											props: { style: { fontSize: '16px', color: '#71717a' }, children: `sessions from ${data.total_contributors} contributor${data.total_contributors === 1 ? '' : 's'}` },
										},
									],
								},
							},
						],
					},
				},
				{
					type: 'div',
					props: {
						style: { marginTop: '32px', fontSize: '16px', color: '#a1a1aa', maxWidth: '600px' },
						children: 'Live sentiment from real Claude Code sessions, scored on-device.',
					},
				},
			],
		},
	};

	return new ImageResponse(html, { width: 1200, height: 630 });
};
