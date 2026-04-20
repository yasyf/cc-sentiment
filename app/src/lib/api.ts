import { env } from '$env/dynamic/private';
import { z } from 'zod';
import type { DataResponse, MyStatResponse, ShareRecord } from './types.js';

const API_BASE_URL = 'https://anetaco--cc-sentiment-api-serve.modal.run';

const ShareRecordSchema = z.object({
	id: z.string(),
	contributor_type: z.enum(['github', 'gpg', 'gist']),
	contributor_id: z.string(),
	avatar_url: z.string().nullable(),
	created_at: z.string(),
});

const MyStatResponseSchema = z.object({
	kind: z.string(),
	percentile: z.number(),
	text: z.string(),
	tweet_text: z.string(),
	total_contributors: z.number(),
});

const DataResponseSchema = z.object({
	timeline: z.array(z.object({
		time: z.string(),
		avg_score: z.number(),
		count: z.number(),
		avg_read_edit_ratio: z.number().nullable().default(null),
		avg_edits_without_prior_read_ratio: z.number().nullable().default(null),
		avg_tool_calls_per_turn: z.number().nullable().default(null)
	})),
	distribution: z.array(z.object({ score: z.number(), count: z.number() })),
	total_records: z.number(),
	total_sessions: z.number(),
	total_contributors: z.number(),
	last_updated: z.string(),
	trend: z.object({
		sentiment_current: z.number(),
		sentiment_previous: z.number(),
		sessions_current: z.number(),
		sessions_previous: z.number(),
		read_edit_current: z.number().nullable(),
		read_edit_previous: z.number().nullable()
	}).default({ sentiment_current: 0, sentiment_previous: 0, sessions_current: 0, sessions_previous: 0, read_edit_current: null, read_edit_previous: null }),
	model_breakdown: z.array(z.object({
		claude_model: z.string(),
		avg_score: z.number(),
		count: z.number(),
		avg_read_edit_ratio: z.number().nullable(),
		avg_write_edit_ratio: z.number().nullable().default(null),
		avg_subagent_count: z.number().nullable().default(null)
	})).default([]),
	avg_read_edit_ratio: z.number().nullable().default(null),
	avg_edits_without_prior_read_ratio: z.number().nullable().default(null),
	avg_tool_calls_per_turn: z.number().nullable().default(null),
	avg_write_edit_ratio: z.number().nullable().default(null),
	avg_subagent_count: z.number().nullable().default(null)
});

export async function fetchData(fetch: typeof globalThis.fetch): Promise<DataResponse> {
	const response = await fetch(`${API_BASE_URL}/data`, {
		headers: { Authorization: `Bearer ${env.DATA_API_TOKEN}` }
	});

	if (!response.ok) {
		throw new Error(`API error: ${response.status} ${response.statusText}`);
	}

	const json: unknown = await response.json();
	return DataResponseSchema.parse(json);
}

export async function fetchShare(
	fetch: typeof globalThis.fetch,
	id: string,
): Promise<ShareRecord | null> {
	const response = await fetch(`${API_BASE_URL}/share/${encodeURIComponent(id)}`);

	if (response.status === 404) return null;
	if (!response.ok) {
		throw new Error(`API error: ${response.status} ${response.statusText}`);
	}

	const json: unknown = await response.json();
	return ShareRecordSchema.parse(json);
}

export async function fetchMyStat(
	fetch: typeof globalThis.fetch,
	contributorId: string,
): Promise<MyStatResponse | null> {
	const response = await fetch(
		`${API_BASE_URL}/my-stats?contributor_id=${encodeURIComponent(contributorId)}`,
	);

	if (response.status === 404) return null;
	if (!response.ok) {
		throw new Error(`API error: ${response.status} ${response.statusText}`);
	}

	const json: unknown = await response.json();
	return MyStatResponseSchema.parse(json);
}
