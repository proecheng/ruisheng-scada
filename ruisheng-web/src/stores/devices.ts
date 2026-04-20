import { defineStore } from 'pinia'
import { ref } from 'vue'

export interface DeviceSummary {
  dev_number: string
  dev_name: string
  state: 'online' | 'offline' | 'warning'
  company?: string
  department?: string
}

export const useDevicesStore = defineStore('devices', () => {
  const list = ref<DeviceSummary[]>([])
  const selectedDevNumber = ref<string | null>(null)

  function setList(items: DeviceSummary[]): void {
    list.value = items
  }
  function select(dev: string | null): void {
    selectedDevNumber.value = dev
  }
  function updateState(dev_number: string, state: DeviceSummary['state']): void {
    const d = list.value.find((x) => x.dev_number === dev_number)
    if (d) d.state = state
  }

  return { list, selectedDevNumber, setList, select, updateState }
})
