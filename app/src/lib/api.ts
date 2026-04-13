import { z } from 'zod';
import { env } from '$env/dynamic/public';

const TimelinePointSchema = z.object({
	time: z.string(),
	avg_score: z.number(),
	count: z.number()
});

const HourlyPointSchema = z.object({
	hour: z.number(),
	avg_score: z.number(),
	count: z.number()
});

const WeekdayPointSchema = z.object({
	dow: z.number(),
	avg_score: z.number(),
	count: z.number()
});

const DistributionPointSchema = z.object({
	score: z.number(),
	count: z.number()
});

const DataResponseSchema = z.object({
	timeline: z.array(TimelinePointSchema),
	hourly: z.array(HourlyPointSchema),
	weekday: z.array(WeekdayPointSchema),
	distribution: z.array(DistributionPointSchema),
	total_records: z.number(),
	last_updated: z.string()
});

export type TimelinePoint = z.infer<typeof TimelinePointSchema>;
export type HourlyPoint = z.infer<typeof HourlyPointSchema>;
export type WeekdayPoint = z.infer<typeof WeekdayPointSchema>;
export type DistributionPoint = z.infer<typeof DistributionPointSchema>;
export type DataResponse = z.infer<typeof DataResponseSchema>;

export async function fetchData(fetch: typeof globalThis.fetch): Promise<DataResponse> {
	const baseUrl = env.PUBLIC_API_URL;
	const response = await fetch(`${baseUrl}/data`);

	if (!response.ok) {
		throw new Error(`API error: ${response.status} ${response.statusText}`);
	}

	const json: unknown = await response.json();
	return DataResponseSchema.parse(json);
}
