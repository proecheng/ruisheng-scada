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

export async function getLatestWaveform(devNumber: string, pointId: number): Promise<WaveformSample> {
  const { data } = await apiClient.get(`/waveforms/${devNumber}/${pointId}`)
  return data.data as WaveformSample
}

export async function analyzeWaveform(
  devNumber: string,
  pointId: number,
  type: AnalysisType,
): Promise<AnalysisResult> {
  const { data } = await apiClient.post('/waveforms/analyze', {
    dev_number: devNumber,
    point_id: pointId,
    type,
  })
  return data.data as AnalysisResult
}
