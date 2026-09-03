import 'axios'

declare module 'axios' {
  interface AxiosRequestConfig {
    ruishengAuthRequest?: boolean
  }

  interface InternalAxiosRequestConfig {
    ruishengAuthRequest?: boolean
  }
}
