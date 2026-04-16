import { env } from '$env/dynamic/private';
import { z } from 'zod';
import type { DataResponse } from './types.js';

const API_BASE_URL = 'https://anetaco--cc-sentiment-api-serve.modal.run';

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
