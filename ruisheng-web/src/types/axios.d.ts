import 'axios'

declare module 'axios' {
  interface AxiosRequestConfig {
    ruishengAuthRequest?: boolean
    ruishengAuthRetried?: boolean
  }

  interface InternalAxiosRequestConfig {
    ruishengAuthRequest?: boolean
    ruishengAuthRetried?: boolean
  }
}
