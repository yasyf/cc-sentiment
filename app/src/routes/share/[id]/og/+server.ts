import { ImageResponse } from '@vercel/og';
import { addCacheTag } from '@vercel/functions';
import { error } from '@sveltejs/kit';
import { fetchData, fetchMyStat, fetchShare } from '$lib/api.js';
import { verdictFor, type VerdictSeverity } from '$lib/verdict.js';
import { describeTrend } from '$lib/trend.js';
import type { RequestHandler } from './$types.js';

const VERDICT_HEX: Record<VerdictSeverity, string> = {
	1: '#dc2626',
	2: '#ea580c',
	3: '#ca8a04',
	4: '#16a34a',
	5: '#059669',
};

export const config = { isr: { expiration: 2_592_000 } };

const WIDTH = 1200;
const HEIGHT = 630;

export const GET: RequestHandler = async ({ fetch, params }) => {
	const record = await fetchShare(fetch, params.id);
	if (!record) throw error(404, 'Share not found');

	addCacheTag(`share:${record.id}`);

	const stat = await fetchMyStat(fetch, record.contributor_id);
	if (!stat) return aggregateCard(fetch);
	if (record.avatar_url) return personalCardWithPhoto(record.contributor_id, record.avatar_url, stat.text);
	return personalCardNoPhoto(record.contributor_id, stat.text);
};

function personalCardWithPhoto(contributorId: string, avatarUrl: string, text: string): ImageResponse {
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
									children: `@${contributorId}`,
								},
							},
							{
								type: 'div',
								props: {
									style: {
										marginTop: '24px',
										fontSize: '52px',
										fontWeight: 700,
										color: '#6366f1',
										lineHeight: 1.15,
									},
									children: `is ${text}.`,
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

function personalCardNoPhoto(contributorId: string, text: string): ImageResponse {
	const html = {
		type: 'div',
		props: {
			style: {
				display: 'flex',
				flexDirection: 'column',
				justifyContent: 'center',
				alignItems: 'center',
				textAlign: 'center',
				width: '100%',
				height: '100%',
				padding: '60px 80px',
				backgroundColor: '#fafafa',
				fontFamily: 'Inter, system-ui, sans-serif',
			},
			children: [
				{
					type: 'span',
					props: {
						style: {
							fontSize: '28px',
							color: '#71717a',
							letterSpacing: '0.02em',
						},
						children: `@${contributorId}`,
					},
				},
				{
					type: 'div',
					props: {
						style: {
							marginTop: '32px',
							fontSize: '64px',
							fontWeight: 700,
							color: '#6366f1',
							lineHeight: 1.15,
						},
						children: `is ${text}.`,
					},
				},
				{
					type: 'div',
					props: {
						style: {
							marginTop: '48px',
							fontSize: '22px',
							color: '#71717a',
						},
						children: 'cc-sentiment · sentiments.cc',
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

	const trendText = describeTrend(data.trend.sentiment_current, data.trend.sentiment_previous);

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
