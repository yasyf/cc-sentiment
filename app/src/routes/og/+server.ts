import { ImageResponse } from '@vercel/og';
import { addCacheTag } from '@vercel/functions';
import { fetchData } from '$lib/api.js';
import { verdictFor, type VerdictSeverity } from '$lib/verdict.js';
import type { RequestHandler } from './$types.js';

const VERDICT_HEX: Record<VerdictSeverity, string> = {
	1: '#dc2626',
	2: '#ea580c',
	3: '#ca8a04',
	4: '#16a34a',
	5: '#059669',
};

export const config = { isr: { expiration: 604800 } };

const WIDTH = 1200;
const HEIGHT = 630;

export const GET: RequestHandler = async ({ fetch, url }) => {
	const u = url.searchParams.get('u');
	const t = url.searchParams.get('t');

	if (u && t) {
		addCacheTag(`user:${u}`);
		return personalCard(u, t);
	}

	addCacheTag('dashboard');
	return aggregateCard(fetch);
};

function personalCard(u: string, t: string): ImageResponse {
	const avatarUrl = `https://github.com/${encodeURIComponent(u)}.png?size=400`;

	const html = {
		type: 'div',
		props: {
			style: {
				display: 'flex',
				width: '100%',
				height: '100%',
				padding: '60px 80px',
				backgroundColor: '#fafafa',
				fontFamily: 'Inter, system-ui, sans-serif',
				alignItems: 'center',
				gap: '64px',
			},
			children: [
				{
					type: 'img',
					props: {
						src: avatarUrl,
						width: 320,
						height: 320,
						style: {
							borderRadius: '50%',
							border: '6px solid #e4e4e7',
						},
					},
				},
				{
					type: 'div',
					props: {
						style: { display: 'flex', flexDirection: 'column', flex: 1 },
						children: [
							{
								type: 'span',
								props: {
									style: {
										fontSize: '24px',
										color: '#71717a',
										letterSpacing: '0.02em',
									},
									children: `@${u}`,
								},
							},
							{
								type: 'div',
								props: {
									style: {
										marginTop: '24px',
										fontSize: '52px',
										fontWeight: 700,
										color: '#18181b',
										lineHeight: 1.15,
										display: 'flex',
										flexWrap: 'wrap',
									},
									children: [
										{
											type: 'span',
											props: { style: { color: '#18181b' }, children: 'is ' },
										},
										{
											type: 'span',
											props: { style: { color: '#6366f1' }, children: `${t}.` },
										},
									],
								},
							},
							{
								type: 'div',
								props: {
									style: {
										marginTop: '40px',
										fontSize: '22px',
										color: '#71717a',
									},
									children: 'cc-sentiment · sentiments.cc',
								},
							},
						],
					},
				},
			],
		},
	};

	return new ImageResponse(html, { width: WIDTH, height: HEIGHT });
}

async function aggregateCard(fetch: typeof globalThis.fetch): Promise<ImageResponse> {
	const data = await fetchData(fetch);

	const total = data.distribution.reduce((s, d) => s + d.count, 0);
	const avg = total > 0
		? (data.distribution.reduce((s, d) => s + d.score * d.count, 0) / total).toFixed(1)
		: '?';

	const { text: verdict, severity } = verdictFor(Number(avg));
	const verdictColor = VERDICT_HEX[severity];

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

	return new ImageResponse(html, { width: WIDTH, height: HEIGHT });
}
