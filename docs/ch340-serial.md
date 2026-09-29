# CH340 RS485 转换器

现有 FTDI 0403:6001 继续使用 schema_version=1 和真实 USB 序列号。无序列号的 CH340 1A86:7523 使用 schema_version=2，adapter 中以 instance_id 替代 serial_number；该值必须来自现场 usbipd state 的完整 Windows InstanceId，并经设备用途确认。可选的 `identity_policy: "single_present_device"` 允许在已批准实例消失且当前恰好只有一个相同 VID/PID 设备时自动重绑定到新的 InstanceId，并把 `configured_instance_id`、实际 `instance_id`、BusId 写入受保护审计和状态文件；如果发现多个相同设备则 fail-closed。省略该字段仍为 `exact_instance` 兼容模式。其余采集参数和固定串口别名保持不变。

挂载入口先唯一匹配 Windows 设备实例，再以实际 usbipd BusId 匹配当前唯一活跃 VHCI 记录，最后只接受该 USB 路径下厂商/产品号一致的串口节点。CH340 加载 ch341 驱动，FTDI 加载 ftdi_sio。拒绝重复实例、错设备、无活跃挂载和多活跃挂载。程序不打开第二个串口读者。

CH340 的 Windows 实例可能依赖电脑USB插口。启用 `single_present_device` 后，换电脑或换插口只要现场同时只有一个相同型号转换器即可自动适配新的 COM 号；多设备时不会自动选择，需移除其他转换器或人工确认配置。

硬件ready记录在 schema_version=2 中携带 instance_id。桌面恢复入口同时核对受保护配置中的实例、设备型号、别名及20秒新鲜度；仍只恢复明确的串口缺失启动失败。正常运行网关的串口重连由原应用处理。

部署需签名校验、旧文件哈希检查、维护锁、配置和任务备份、原子替换与失败回退。此变更不修改Modbus帧、CRC、读写超时或DO输出。
