import { DateTime } from 'luxon';
import type { TimelinePoint } from './types.js';
import { chartTheme } from './chart-theme.js';

export type DayPartKey = 'late' | 'morning' | 'afternoon' | 'evening';

export interface DayPart {
	key: DayPartKey;
	label: string;
	start: number;
	end: number;
	mid: number;
}

export const DAY_PARTS: readonly DayPart[] = [
	{ key: 'late', label: 'late', start: 22, end: 6, mid: 2 },
	{ key: 'morning', label: 'morning', start: 6, end: 12, mid: 9 },
	{ key: 'afternoon', label: 'afternoon', start: 12, end: 18, mid: 15 },
	{ key: 'evening', label: 'evening', start: 18, end: 22, mid: 20 }
] as const;

export const DAY_PART_EMOJI: Record<DayPartKey, string> = {
	morning: '🌅',
	afternoon: '☀️',
	evening: '🌆',
	late: '🌙'
};


export function dayPartFor(hour: number): DayPart {
	for (const part of DAY_PARTS) {
		const hit = part.start < part.end
			? hour >= part.start && hour < part.end
			: hour >= part.start || hour < part.end;
		if (hit) return part;
	}
	throw new Error(`no day part for hour ${hour}`);
}

export interface DayPartBucket {
	time: string;
	date: string;
	part: DayPartKey;
	label: string;
	avg_score: number;
	count: number;
	avg_read_edit_ratio: number | null;
	avg_edits_without_prior_read_ratio: number | null;
	avg_tool_calls_per_turn: number | null;
}

interface Accumulator {
	score: number;
	count: number;
	readEditSum: number;
	readEditCount: number;
	editsWithoutReadSum: number;
	editsWithoutReadCount: number;
	toolCallsSum: number;
	toolCallsCount: number;
}

function fresh(): Accumulator {
	return {
		score: 0,
		count: 0,
		readEditSum: 0,
		readEditCount: 0,
		editsWithoutReadSum: 0,
		editsWithoutReadCount: 0,
		toolCallsSum: 0,
		toolCallsCount: 0
	};
}

export interface DayBucket {
	time: string;
	date: string;
	avg_score: number;
	count: number;
	avg_read_edit_ratio: number | null;
	avg_edits_without_prior_read_ratio: number | null;
	avg_tool_calls_per_turn: number | null;
}

export function bucketByDay(points: TimelinePoint[], zone: string): DayBucket[] {
	const acc = new Map<string, Accumulator & { date: string }>();
	for (const point of points) {
		const dt = DateTime.fromISO(point.time, { zone });
		if (!dt.isValid) continue;
		const date = dt.toISODate();
		if (!date) continue;
		const entry = acc.get(date) ?? { date, ...fresh() };
		entry.score += point.avg_score * point.count;
		entry.count += point.count;
		if (point.avg_read_edit_ratio != null) {
			entry.readEditSum += point.avg_read_edit_ratio * point.count;
			entry.readEditCount += point.count;
		}
		if (point.avg_edits_without_prior_read_ratio != null) {
			entry.editsWithoutReadSum += point.avg_edits_without_prior_read_ratio * point.count;
			entry.editsWithoutReadCount += point.count;
		}
		if (point.avg_tool_calls_per_turn != null) {
			entry.toolCallsSum += point.avg_tool_calls_per_turn * point.count;
			entry.toolCallsCount += point.count;
		}
		acc.set(date, entry);
	}
	return Array.from(acc.values())
		.map((entry) => {
			const ts = DateTime.fromISO(entry.date, { zone }).set({ hour: 12, minute: 0, second: 0, millisecond: 0 });
			return {
				time: ts.toISO() ?? '',
				date: entry.date,
				avg_score: entry.count > 0 ? entry.score / entry.count : 0,
				count: entry.count,
				avg_read_edit_ratio: entry.readEditCount > 0 ? entry.readEditSum / entry.readEditCount : null,
				avg_edits_without_prior_read_ratio:
					entry.editsWithoutReadCount > 0 ? entry.editsWithoutReadSum / entry.editsWithoutReadCount : null,
				avg_tool_calls_per_turn:
					entry.toolCallsCount > 0 ? entry.toolCallsSum / entry.toolCallsCount : null
			};
		})
		.toSorted((a, b) => new Date(a.time).getTime() - new Date(b.time).getTime());
}

export interface SmoothOpts {
	halfWindow: number;
	priorWeight?: number;
	priorValue?: number;
}

export function smoothSeries<T extends { count: number }>(
	buckets: readonly T[],
	valueOf: (b: T) => number | null,
	opts: SmoothOpts
): (number | null)[] {
	const { halfWindow, priorWeight = 0, priorValue = 0 } = opts;
	if (buckets.length === 0) return [];
	const denom = halfWindow + 1;
	return buckets.map((_, i) => {
		let neighborCountWeight = 0;
		let weightSum = priorWeight;
		let valueSum = priorWeight * priorValue;
		const lo = Math.max(0, i - halfWindow);
		const hi = Math.min(buckets.length - 1, i + halfWindow);
		for (let j = lo; j <= hi; j++) {
			const v = valueOf(buckets[j]);
			if (v == null) continue;
			const kernel = 1 - Math.abs(i - j) / denom;
			const w = kernel * buckets[j].count;
			neighborCountWeight += w;
			valueSum += v * w;
			weightSum += w;
		}
		if (neighborCountWeight === 0) return null;
		return valueSum / weightSum;
	});
}

export function filterWindow<T extends { time: string }>(
	buckets: readonly T[],
	days: number,
	zone: string
): T[] {
	if (buckets.length === 0) return [];
	const last = DateTime.fromISO(buckets[buckets.length - 1].time, { zone });
	if (!last.isValid) return [...buckets];
	const cutoff = last.startOf('day').minus({ days: days - 1 }).toMillis();
	return buckets.filter((b) => new Date(b.time).getTime() >= cutoff);
}

export function bucketByDayPart(points: TimelinePoint[], zone: string): DayPartBucket[] {
	const acc = new Map<string, { date: string; part: DayPart; data: Accumulator }>();
	for (const point of points) {
		const dt = DateTime.fromISO(point.time, { zone });
		if (!dt.isValid) continue;
		const part = dayPartFor(dt.hour);
		const anchor = part.key === 'late' && dt.hour < 6 ? dt.minus({ days: 1 }) : dt;
		const date = anchor.toISODate();
		if (!date) continue;
		const key = `${date}::${part.key}`;
		const entry = acc.get(key) ?? { date, part, data: fresh() };
		entry.data.score += point.avg_score * point.count;
		entry.data.count += point.count;
		if (point.avg_read_edit_ratio != null) {
			entry.data.readEditSum += point.avg_read_edit_ratio * point.count;
			entry.data.readEditCount += point.count;
		}
		if (point.avg_edits_without_prior_read_ratio != null) {
			entry.data.editsWithoutReadSum += point.avg_edits_without_prior_read_ratio * point.count;
			entry.data.editsWithoutReadCount += point.count;
		}
		if (point.avg_tool_calls_per_turn != null) {
			entry.data.toolCallsSum += point.avg_tool_calls_per_turn * point.count;
			entry.data.toolCallsCount += point.count;
		}
		acc.set(key, entry);
	}
	return Array.from(acc.values())
		.map(({ date, part, data }) => {
			const base = DateTime.fromISO(date, { zone });
			const ts = part.key === 'late'
				? base.plus({ days: 1 }).set({ hour: part.mid, minute: 0, second: 0, millisecond: 0 })
				: base.set({ hour: part.mid, minute: 0, second: 0, millisecond: 0 });
			return {
				time: ts.toISO() ?? '',
				date,
				part: part.key,
				label: part.label,
				avg_score: data.count > 0 ? data.score / data.count : 0,
				count: data.count,
				avg_read_edit_ratio: data.readEditCount > 0 ? data.readEditSum / data.readEditCount : null,
				avg_edits_without_prior_read_ratio:
					data.editsWithoutReadCount > 0 ? data.editsWithoutReadSum / data.editsWithoutReadCount : null,
				avg_tool_calls_per_turn: data.toolCallsCount > 0 ? data.toolCallsSum / data.toolCallsCount : null
			};
		})
		.toSorted((a, b) => new Date(a.time).getTime() - new Date(b.time).getTime());
}

export function fillMissingDayParts(
	buckets: readonly DayPartBucket[],
	days: number,
	zone: string
): DayPartBucket[] {
	if (buckets.length === 0) return [];
	const lastTime = DateTime.fromISO(buckets[buckets.length - 1].time, { zone });
	if (!lastTime.isValid) return [...buckets];
	const lastDate = lastTime.startOf('day');
	const firstDate = lastDate.minus({ days: days - 1 });

	const byKey = new Map(buckets.map((b) => [`${b.date}::${b.part}`, b]));
	const lastByPart = new Map<DayPartKey, DayPartBucket>();
	const result: DayPartBucket[] = [];

	for (const b of buckets) {
		if (DateTime.fromISO(b.date, { zone }) < firstDate) {
			result.push(b);
			lastByPart.set(b.part, b);
		}
	}

	let cursor = firstDate;
	while (cursor <= lastDate) {
		const date = cursor.toISODate();
		if (!date) {
			cursor = cursor.plus({ days: 1 });
			continue;
		}
		for (const part of DAY_PARTS) {
			const existing = byKey.get(`${date}::${part.key}`);
			if (existing) {
				result.push(existing);
				lastByPart.set(part.key, existing);
				continue;
			}
			const prior = lastByPart.get(part.key);
			if (!prior) continue;
			const ts = part.key === 'late'
				? cursor.plus({ days: 1 }).set({ hour: part.mid, minute: 0, second: 0, millisecond: 0 })
				: cursor.set({ hour: part.mid, minute: 0, second: 0, millisecond: 0 });
			result.push({
				time: ts.toISO() ?? '',
				date,
				part: part.key,
				label: part.label,
				avg_score: prior.avg_score,
				count: 0,
				avg_read_edit_ratio: null,
				avg_edits_without_prior_read_ratio: null,
				avg_tool_calls_per_turn: null
			});
		}
		cursor = cursor.plus({ days: 1 });
	}

	return result.toSorted((a, b) => new Date(a.time).getTime() - new Date(b.time).getTime());
}

export function dayBoundaryAnnotations(points: { time: string }[], zone: string): Record<string, object> {
	if (points.length === 0) return {};
	const first = DateTime.fromISO(points[0].time, { zone });
	const last = DateTime.fromISO(points[points.length - 1].time, { zone });
	if (!first.isValid || !last.isValid) return {};
	const annotations: Record<string, object> = {};
	let cursor = first.startOf('day').plus({ days: 1 });
	while (cursor <= last) {
		const iso = cursor.toISO();
		if (iso) {
			annotations[`day-${cursor.toISODate()}`] = {
				type: 'line',
				xMin: iso,
				xMax: iso,
				borderColor: chartTheme.DAY_BOUNDARY,
				borderWidth: 1.5,
				adjustScaleRange: false,
				label: { display: false }
			};
		}
		cursor = cursor.plus({ days: 1 });
	}
	return annotations;
}

function partBoundsOn(date: DateTime, part: DayPart): { start: DateTime; end: DateTime } {
	const start = date.set({ hour: part.start, minute: 0, second: 0, millisecond: 0 });
	const end = part.start < part.end
		? date.set({ hour: part.end, minute: 0, second: 0, millisecond: 0 })
		: date.plus({ days: 1 }).set({ hour: part.end, minute: 0, second: 0, millisecond: 0 });
	return { start, end };
}

function partFor(key: DayPartKey): DayPart {
	const part = DAY_PARTS.find((p) => p.key === key);
	if (!part) throw new Error(`unknown day part key: ${key}`);
	return part;
}

export function dayPartRange(buckets: readonly DayPartBucket[], zone: string): { startISO: string; endISO: string } | null {
	if (buckets.length === 0) return null;
	const firstBucket = buckets[0];
	const lastBucket = buckets[buckets.length - 1];
	const firstDate = DateTime.fromISO(firstBucket.date, { zone });
	const lastDate = DateTime.fromISO(lastBucket.date, { zone });
	if (!firstDate.isValid || !lastDate.isValid) return null;
	const { start } = partBoundsOn(firstDate, partFor(firstBucket.part));
	const { end } = partBoundsOn(lastDate, partFor(lastBucket.part));
	const startISO = start.toISO();
	const endISO = end.toISO();
	if (!startISO || !endISO) return null;
	return { startISO, endISO };
}

export function dayPartBandAnnotations(buckets: readonly DayPartBucket[], zone: string): Record<string, object> {
	const range = dayPartRange(buckets, zone);
	if (!range) return {};
	const first = DateTime.fromISO(range.startISO, { zone });
	const last = DateTime.fromISO(range.endISO, { zone });
	if (!first.isValid || !last.isValid) return {};
	const annotations: Record<string, object> = {};
	let cursor = first.startOf('day').minus({ days: 1 });
	const limit = last.endOf('day');
	while (cursor <= limit) {
		const date = cursor.toISODate();
		if (!date) {
			cursor = cursor.plus({ days: 1 });
			continue;
		}
		for (const part of DAY_PARTS) {
			const { start: xMin, end: xMax } = partBoundsOn(cursor, part);
			const clampedMin = xMin < first ? first : xMin;
			const clampedMax = xMax > last ? last : xMax;
			if (clampedMax <= clampedMin) continue;
			const xMinIso = clampedMin.toISO();
			const xMaxIso = clampedMax.toISO();
			if (!xMinIso || !xMaxIso) continue;
			annotations[`band-${date}-${part.key}`] = {
				type: 'box',
				xMin: xMinIso,
				xMax: xMaxIso,
				backgroundColor: chartTheme.DAY_PART_TINT[part.key],
				borderWidth: 0,
				adjustScaleRange: false,
				drawTime: 'beforeDatasetsDraw'
			};
		}
		cursor = cursor.plus({ days: 1 });
	}
	return annotations;
}
