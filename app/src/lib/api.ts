import { z } from 'zod';

const API_BASE_URL = 'https://anetaco--cc-sentiment-api-serve.modal.run';

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

export async function fetchData(): Promise<DataResponse> {
	const response = await globalThis.fetch(`${API_BASE_URL}/data`);

	if (!response.ok) {
		throw new Error(`API error: ${response.status} ${response.statusText}`);
	}

	const json: unknown = await response.json();
	return DataResponseSchema.parse(json);
}
