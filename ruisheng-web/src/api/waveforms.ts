import { apiClient } from '@/api/client'

export interface WaveformSample {
  ts: string
  samples: number[]
  sample_rate_hz: number
}

export type AnalysisType = 'FFT' | 'OPM'

export interface AnalysisResult {
  type: AnalysisType
  peaks: Array<{ freq_hz: number; amplitude: number }>
  raw: number[]
}

function defaultWindow(): { from: string; to: string } {
  return {
    from: new Date(Date.now() - 24 * 3600000).toISOString(),
    to: new Date().toISOString(),
  }
}

export async function getLatestWaveform(devNumber: string, pointId: number): Promise<WaveformSample> {
  const { data } = await apiClient.get(`/waveforms/${devNumber}/${pointId}`, {
    params: defaultWindow(),
  })
  const payload = data.data as Partial<WaveformSample> & {
    waveforms?: Array<{ recorded_at?: string; packet_count?: number; sample_time_decisec?: number }>
  }
  const latest = payload.waveforms?.[0]
  const sampleTimeSec = Number(latest?.sample_time_decisec ?? 0) * 0.1
  const packetCount = Number(latest?.packet_count ?? 0)
  return {
    ts: payload.ts ?? latest?.recorded_at ?? new Date(0).toISOString(),
    samples: payload.samples ?? [],
    sample_rate_hz:
      payload.sample_rate_hz ?? (sampleTimeSec > 0 ? packetCount / sampleTimeSec : 0),
  }
}

export async function analyzeWaveform(
  devNumber: string,
  pointId: number,
  type: AnalysisType,
): Promise<AnalysisResult> {
  const { data } = await apiClient.post('/waveforms/analyze', null, {
    params: {
      dev_number: devNumber,
      point_id: pointId,
      ...defaultWindow(),
    },
  })
  const payload = data.data as
    | AnalysisResult
    | { freqs?: number[]; magnitudes?: number[]; raw?: number[] }
  if ('peaks' in payload) return payload
  const freqs = payload.freqs ?? []
  const mags = payload.magnitudes ?? []
  return {
    type,
    peaks: freqs.map((freq, i) => ({ freq_hz: freq, amplitude: Number(mags[i] ?? 0) })),
    raw: payload.raw ?? mags,
  }
}
