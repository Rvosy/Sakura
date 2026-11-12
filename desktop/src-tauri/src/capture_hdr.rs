//! HDR 桌面以 scRGB 浮点取图，按系统 SDR 白电平转换为聊天使用的 sRGB 图片。

use half::f16;
use image::RgbaImage;
use std::{sync::mpsc, time::Duration};
use windows::{
    core::{factory, Error, IInspectable, Interface, Result},
    Foundation::TypedEventHandler,
    Graphics::{
        Capture::{Direct3D11CaptureFramePool, GraphicsCaptureItem},
        DirectX::{Direct3D11::IDirect3DDevice, DirectXPixelFormat},
    },
    Win32::{
        Devices::Display::*,
        Foundation::{E_FAIL, HMODULE, WIN32_ERROR},
        Graphics::{
            Direct3D::D3D_DRIVER_TYPE_UNKNOWN,
            Direct3D11::*,
            Dxgi::{Common::*, *},
        },
        System::WinRT::{
            Direct3D11::{CreateDirect3D11DeviceFromDXGIDevice, IDirect3DDxgiInterfaceAccess},
            Graphics::Capture::IGraphicsCaptureItemInterop,
        },
    },
};

pub fn capture(device_name: &str) -> Result<Option<RgbaImage>> {
    unsafe {
        let factory: IDXGIFactory1 = CreateDXGIFactory1()?;
        let mut adapter_index = 0;
        loop {
            let adapter = match factory.EnumAdapters1(adapter_index) {
                Ok(adapter) => adapter,
                Err(error) if error.code() == DXGI_ERROR_NOT_FOUND => break,
                Err(error) => return Err(error),
            };
            adapter_index += 1;
            let mut output_index = 0;
            loop {
                let output = match adapter.EnumOutputs(output_index) {
                    Ok(output) => output,
                    Err(error) if error.code() == DXGI_ERROR_NOT_FOUND => break,
                    Err(error) => return Err(error),
                };
                output_index += 1;
                let descriptor = output.GetDesc()?;
                if display_name(&descriptor.DeviceName) != device_name {
                    continue;
                }
                let color = output.cast::<IDXGIOutput6>()?.GetDesc1()?;
                if color.ColorSpace != DXGI_COLOR_SPACE_RGB_FULL_G2084_NONE_P2020 {
                    return Ok(None);
                }
                let white_scale = sdr_white_scale(device_name)?;
                return capture_output(&adapter, &output, white_scale).map(Some);
            }
        }
        Err(Error::new(
            E_FAIL,
            format!("截图显示器已断开：{device_name}"),
        ))
    }
}

fn display_name(value: &[u16]) -> String {
    let end = value
        .iter()
        .position(|value| *value == 0)
        .unwrap_or(value.len());
    String::from_utf16_lossy(&value[..end])
}

unsafe fn sdr_white_scale(device_name: &str) -> Result<f32> {
    let (mut path_count, mut mode_count) = (0, 0);
    GetDisplayConfigBufferSizes(QDC_ONLY_ACTIVE_PATHS, &mut path_count, &mut mode_count).ok()?;
    let mut paths = vec![DISPLAYCONFIG_PATH_INFO::default(); path_count as usize];
    let mut modes = vec![DISPLAYCONFIG_MODE_INFO::default(); mode_count as usize];
    QueryDisplayConfig(
        QDC_ONLY_ACTIVE_PATHS,
        &mut path_count,
        paths.as_mut_ptr(),
        &mut mode_count,
        modes.as_mut_ptr(),
        None,
    )
    .ok()?;
    for path in &paths[..path_count as usize] {
        let mut source = DISPLAYCONFIG_SOURCE_DEVICE_NAME {
            header: DISPLAYCONFIG_DEVICE_INFO_HEADER {
                r#type: DISPLAYCONFIG_DEVICE_INFO_GET_SOURCE_NAME,
                size: size_of::<DISPLAYCONFIG_SOURCE_DEVICE_NAME>() as u32,
                adapterId: path.sourceInfo.adapterId,
                id: path.sourceInfo.id,
            },
            ..Default::default()
        };
        WIN32_ERROR(DisplayConfigGetDeviceInfo(&mut source.header) as u32).ok()?;
        if display_name(&source.viewGdiDeviceName) != device_name {
            continue;
        }
        let mut white = DISPLAYCONFIG_SDR_WHITE_LEVEL {
            header: DISPLAYCONFIG_DEVICE_INFO_HEADER {
                r#type: DISPLAYCONFIG_DEVICE_INFO_GET_SDR_WHITE_LEVEL,
                size: size_of::<DISPLAYCONFIG_SDR_WHITE_LEVEL>() as u32,
                adapterId: path.targetInfo.adapterId,
                id: path.targetInfo.id,
            },
            ..Default::default()
        };
        WIN32_ERROR(DisplayConfigGetDeviceInfo(&mut white.header) as u32).ok()?;
        // Windows 的 1000 对应 scRGB 的 1.0，即 80 nits。
        return Ok(white.SDRWhiteLevel as f32 / 1000.0);
    }
    Err(Error::new(
        E_FAIL,
        format!("未找到显示器的 SDR 白电平：{device_name}"),
    ))
}

unsafe fn capture_output(
    adapter: &IDXGIAdapter1,
    output: &IDXGIOutput,
    white_scale: f32,
) -> Result<RgbaImage> {
    let (mut device, mut context) = (None, None);
    D3D11CreateDevice(
        adapter,
        D3D_DRIVER_TYPE_UNKNOWN,
        HMODULE::default(),
        D3D11_CREATE_DEVICE_BGRA_SUPPORT,
        None,
        D3D11_SDK_VERSION,
        Some(&mut device),
        None,
        Some(&mut context),
    )?;
    let (device, context) = (device.unwrap(), context.unwrap());
    let interop = factory::<GraphicsCaptureItem, IGraphicsCaptureItemInterop>()?;
    let item: GraphicsCaptureItem = interop.CreateForMonitor(output.GetDesc()?.Monitor)?;
    let dxgi = device.cast::<IDXGIDevice>()?;
    let capture_device: IDirect3DDevice = CreateDirect3D11DeviceFromDXGIDevice(&dxgi)?.cast()?;
    let pool = Direct3D11CaptureFramePool::CreateFreeThreaded(
        &capture_device,
        DirectXPixelFormat::R16G16B16A16Float,
        1,
        item.Size()?,
    )?;
    let result = (|| {
        let session = pool.CreateCaptureSession(&item)?;
        let result = (|| {
            let (sender, receiver) = mpsc::channel();
            let token = pool.FrameArrived(&TypedEventHandler::<
                Direct3D11CaptureFramePool,
                IInspectable,
            >::new(move |pool, _| {
                let _ = sender.send(pool.as_ref().unwrap().TryGetNextFrame());
                Ok(())
            }))?;
            // 边框和光标隐藏是可选能力，不影响取图。
            let _ = session.SetIsBorderRequired(false);
            let _ = session.SetIsCursorCaptureEnabled(false);
            session.StartCapture()?;
            // 沿用 xcap WGC 的单帧等待时间，避免没有帧到达时占住截图操作。
            let frame = receiver
                .recv_timeout(Duration::from_secs(3))
                .map_err(|error| Error::new(E_FAIL, error.to_string()))??;
            let result = (|| {
                pool.RemoveFrameArrived(token)?;
                let access = frame.Surface()?.cast::<IDirect3DDxgiInterfaceAccess>()?;
                let source: ID3D11Texture2D = access.GetInterface()?;
                let mut descriptor = D3D11_TEXTURE2D_DESC::default();
                source.GetDesc(&mut descriptor);
                descriptor.Usage = D3D11_USAGE_STAGING;
                descriptor.BindFlags = 0;
                descriptor.CPUAccessFlags = D3D11_CPU_ACCESS_READ.0 as u32;
                descriptor.MiscFlags = 0;
                let mut staging = None;
                device.CreateTexture2D(&descriptor, None, Some(&mut staging))?;
                let staging = staging.unwrap();
                context.CopyResource(&staging, &source);
                let mut mapped = D3D11_MAPPED_SUBRESOURCE::default();
                context.Map(&staging, 0, D3D11_MAP_READ, 0, Some(&mut mapped))?;
                let image = read_pixels(&descriptor, &mapped, white_scale);
                context.Unmap(&staging, 0);
                Ok(image)
            })();
            let _ = frame.Close();
            result
        })();
        let _ = session.Close();
        result
    })();
    let _ = pool.Close();
    result
}

unsafe fn read_pixels(
    descriptor: &D3D11_TEXTURE2D_DESC,
    mapped: &D3D11_MAPPED_SUBRESOURCE,
    white_scale: f32,
) -> RgbaImage {
    let pixel_bytes = 8;
    let mut image = RgbaImage::new(descriptor.Width, descriptor.Height);
    for y in 0..descriptor.Height {
        let row = std::slice::from_raw_parts(
            mapped
                .pData
                .cast::<u8>()
                .add(y as usize * mapped.RowPitch as usize),
            descriptor.Width as usize * pixel_bytes,
        );
        for (x, bytes) in row.chunks_exact(pixel_bytes).enumerate() {
            let linear = std::array::from_fn(|channel| {
                f16::from_le_bytes([bytes[channel * 2], bytes[channel * 2 + 1]]).to_f32()
            });
            let rgb = scrgb_to_srgb(linear, white_scale);
            image.put_pixel(x as u32, y, image::Rgba([rgb[0], rgb[1], rgb[2], 255]));
        }
    }
    image
}

fn scrgb_to_srgb(rgb: [f32; 3], white_scale: f32) -> [u8; 3] {
    rgb.map(|value| {
        let linear = (value / white_scale).clamp(0.0, 1.0);
        let encoded = if linear <= 0.0031308 {
            12.92 * linear
        } else {
            1.055 * linear.powf(1.0 / 2.4) - 0.055
        };
        (encoded * 255.0).round() as u8
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    #[ignore = "需要开启 HDR 的真实 Windows 显示器"]
    fn capture_hdr_monitor() {
        for monitor in xcap::Monitor::all().unwrap() {
            let name = monitor.name().unwrap();
            if let Some(image) = capture(&name).unwrap() {
                assert_eq!(
                    image.dimensions(),
                    (monitor.width().unwrap(), monitor.height().unwrap())
                );
                let path = std::env::temp_dir().join("sakura-hdr-capture.png");
                image.save(&path).unwrap();
                println!(
                    "HDR capture: {name}, {}x{}, {}",
                    image.width(),
                    image.height(),
                    path.display()
                );
                return;
            }
        }
        panic!("请先开启显示器 HDR，再运行此检查");
    }

    #[test]
    fn sdr_ui_colors_survive_hdr_white_level_boost() {
        for white_scale in [1.0, 2.5, 5.0] {
            assert_eq!(
                scrgb_to_srgb([0.0, 0.2158605 * white_scale, white_scale], white_scale),
                [0, 128, 255]
            );
            assert_eq!(
                scrgb_to_srgb([white_scale, 0.0, 0.0], white_scale),
                [255, 0, 0]
            );
        }
        assert_eq!(scrgb_to_srgb([-0.1, 2.0, 8.0], 1.0), [0, 255, 255]);
    }
}
