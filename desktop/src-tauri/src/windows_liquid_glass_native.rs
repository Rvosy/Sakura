//! Windows implementation for the safety-gated WP-3-03D single GPU pipeline.
//!
//! This module owns one WGC monitor session, one D3D11 device, two intermediate
//! textures and one composition swap chain. It never creates a DWM effect graph.

use std::sync::{Arc, Mutex};

use windows::{
    core::{factory, IInspectable, Interface},
    Foundation::TypedEventHandler,
    Graphics::{
        Capture::{Direct3D11CaptureFramePool, GraphicsCaptureItem, GraphicsCaptureSession},
        DirectX::{Direct3D11::IDirect3DDevice, DirectXPixelFormat},
    },
    Win32::{
        Foundation::{HMODULE, HWND, RECT},
        Graphics::{
            Direct3D::{D3D_DRIVER_TYPE_HARDWARE, D3D_PRIMITIVE_TOPOLOGY_TRIANGLELIST},
            Direct3D11::{
                D3D11CreateDevice, ID3D11Buffer, ID3D11Device, ID3D11DeviceContext,
                ID3D11PixelShader, ID3D11RenderTargetView, ID3D11SamplerState,
                ID3D11ShaderResourceView, ID3D11Texture2D, ID3D11VertexShader,
                D3D11_BIND_CONSTANT_BUFFER, D3D11_BIND_RENDER_TARGET, D3D11_BIND_SHADER_RESOURCE,
                D3D11_BUFFER_DESC, D3D11_COMPARISON_NEVER, D3D11_CREATE_DEVICE_BGRA_SUPPORT,
                D3D11_FILTER_MIN_MAG_MIP_LINEAR, D3D11_SAMPLER_DESC, D3D11_SDK_VERSION,
                D3D11_TEXTURE2D_DESC, D3D11_TEXTURE_ADDRESS_CLAMP, D3D11_USAGE_DEFAULT,
                D3D11_VIEWPORT,
            },
            Dxgi::{
                Common::{
                    DXGI_ALPHA_MODE_PREMULTIPLIED, DXGI_FORMAT_B8G8R8A8_UNORM, DXGI_SAMPLE_DESC,
                },
                CreateDXGIFactory2, IDXGIDevice, IDXGIFactory2, IDXGISwapChain1,
                DXGI_CREATE_FACTORY_FLAGS, DXGI_PRESENT, DXGI_SCALING_STRETCH,
                DXGI_SWAP_CHAIN_DESC1, DXGI_SWAP_EFFECT_FLIP_SEQUENTIAL,
                DXGI_USAGE_RENDER_TARGET_OUTPUT,
            },
            Gdi::{
                GetMonitorInfoW, MonitorFromWindow, HMONITOR, MONITORINFO, MONITOR_DEFAULTTONEAREST,
            },
        },
        System::WinRT::{
            Composition::ICompositorInterop,
            Direct3D11::{CreateDirect3D11DeviceFromDXGIDevice, IDirect3DDxgiInterfaceAccess},
            Graphics::Capture::IGraphicsCaptureItemInterop,
        },
        UI::WindowsAndMessaging::GetWindowRect,
    },
    UI::Composition::{
        CompositionSurfaceBrush, Compositor, ContainerVisual, SpriteVisual, VisualCollection,
    },
};
use windows_numerics::{Vector2, Vector3};

use crate::windows_liquid_glass::{
    configured_debug_step, DebugStep, SamplingGeometry, BLUR_PIXEL_HLSL, DISPERSION,
    FRESNEL_FACTOR, FRESNEL_HARDNESS, FRESNEL_RANGE, FULLSCREEN_VERTEX_HLSL, GLARE_ANGLE_RADIANS,
    GLARE_CONVERGENCE, GLARE_FACTOR, GLARE_HARDNESS, GLARE_OPPOSITE, GLARE_RANGE,
    LIQUID_PIXEL_HLSL, MAX_CAPTURE_BUFFERS, MAX_SWAP_CHAIN_BUFFERS, REFRACTION_FACTOR,
    REFRACTION_THICKNESS,
};

const _: () = assert!(crate::windows_liquid_glass::ResourceBudget::running().within_limit());

const ERROR_CODE: &str = "LIQUID_GLASS_SINGLE_PIPELINE_FAILED";

#[derive(Debug)]
pub struct NativeError {
    pub code: &'static str,
    pub detail: String,
}

impl NativeError {
    fn at(code: &'static str, error: impl std::fmt::Display) -> Self {
        Self {
            code,
            detail: error.to_string(),
        }
    }
}

impl From<windows::core::Error> for NativeError {
    fn from(error: windows::core::Error) -> Self {
        Self::at(ERROR_CODE, error)
    }
}

pub struct SinglePipelineController {
    hwnd_value: isize,
    compositor: Compositor,
    children: VisualCollection,
    gaussian_visual: SpriteVisual,
    liquid_visual: SpriteVisual,
    liquid_brush: CompositionSurfaceBrush,
    pipeline: Mutex<Option<NativePipeline>>,
    fused: Arc<std::sync::atomic::AtomicBool>,
    requested_visible: Arc<std::sync::atomic::AtomicBool>,
    ready: Arc<std::sync::atomic::AtomicBool>,
    tint: Arc<Mutex<[f32; 4]>>,
    latest_geometry: Mutex<Option<SamplingGeometry>>,
}

impl SinglePipelineController {
    pub fn install(
        hwnd: HWND,
        compositor: &Compositor,
        container: &ContainerVisual,
        gaussian_visual: &SpriteVisual,
    ) -> Result<Self, NativeError> {
        let children = container
            .Children()
            .map_err(|error| NativeError::at("LIQUID_GLASS_CHILDREN_UNAVAILABLE", error))?;
        let liquid_brush = compositor
            .CreateSurfaceBrush()
            .map_err(|error| NativeError::at("LIQUID_GLASS_SURFACE_BRUSH_FAILED", error))?;
        let liquid_visual = compositor
            .CreateSpriteVisual()
            .map_err(|error| NativeError::at("LIQUID_GLASS_VISUAL_FAILED", error))?;
        liquid_visual
            .SetBrush(&liquid_brush)
            .and_then(|_| liquid_visual.SetIsVisible(false))
            .map_err(|error| NativeError::at("LIQUID_GLASS_VISUAL_INITIALIZE_FAILED", error))?;
        children
            .InsertAbove(&liquid_visual, gaussian_visual)
            .map_err(|error| NativeError::at("LIQUID_GLASS_VISUAL_INSERT_FAILED", error))?;
        Ok(Self {
            hwnd_value: hwnd.0 as isize,
            compositor: compositor.clone(),
            children,
            gaussian_visual: gaussian_visual.clone(),
            liquid_visual,
            liquid_brush,
            pipeline: Mutex::new(None),
            fused: Arc::new(std::sync::atomic::AtomicBool::new(false)),
            requested_visible: Arc::new(std::sync::atomic::AtomicBool::new(false)),
            ready: Arc::new(std::sync::atomic::AtomicBool::new(false)),
            tint: Arc::new(Mutex::new([1.0, 1.0, 1.0, 0.0])),
            latest_geometry: Mutex::new(None),
        })
    }

    pub fn enabled(&self) -> bool {
        !self.fused.load(std::sync::atomic::Ordering::Acquire)
    }

    pub fn set_requested_visible(&self, visible: bool) -> Result<(), NativeError> {
        if visible {
            // WGC has no per-session window exclusion. Excluding the Sakura HWND
            // through the window display-affinity API also removes the pet from normal screenshots,
            // while leaving it included recursively corrupts the liquid input.
            // Fail closed until a capture source can isolate only this pipeline.
            self.requested_visible
                .store(false, std::sync::atomic::Ordering::Release);
            self.ready
                .store(false, std::sync::atomic::Ordering::Release);
            self.pipeline
                .lock()
                .map_err(|_| NativeError::at(ERROR_CODE, "pipeline lock"))?
                .take();
            let _ = self.liquid_visual.SetIsVisible(false);
            let _ = self.gaussian_visual.SetIsVisible(true);
            return Err(NativeError::at(
                "LIQUID_GLASS_CAPTURE_ISOLATION_UNAVAILABLE",
                "WGC cannot exclude Sakura only from its own capture session",
            ));
        }
        self.requested_visible
            .store(false, std::sync::atomic::Ordering::Release);
        self.ready
            .store(false, std::sync::atomic::Ordering::Release);
        self.pipeline
            .lock()
            .map_err(|_| NativeError::at(ERROR_CODE, "pipeline lock"))?
            .take();
        let ready = self.ready.load(std::sync::atomic::Ordering::Acquire);
        if visible && self.enabled() && ready {
            self.liquid_visual
                .SetIsVisible(true)
                .and_then(|_| self.gaussian_visual.SetIsVisible(false))
                .map_err(|error| NativeError::at("LIQUID_GLASS_SHOW_FAILED", error))
        } else {
            self.liquid_visual
                .SetIsVisible(false)
                .and_then(|_| self.gaussian_visual.SetIsVisible(true))
                .map_err(|error| NativeError::at("LIQUID_GLASS_HIDE_FAILED", error))
        }
    }

    pub fn update_tint(&self, rgb: [u8; 3], alpha: f32) -> Result<(), NativeError> {
        let tint = [
            f32::from(rgb[0]) / 255.0,
            f32::from(rgb[1]) / 255.0,
            f32::from(rgb[2]) / 255.0,
            alpha.clamp(0.0, 1.0),
        ];
        *self
            .tint
            .lock()
            .map_err(|_| NativeError::at(ERROR_CODE, "tint lock"))? = tint;
        if let Some(pipeline) = self
            .pipeline
            .lock()
            .map_err(|_| NativeError::at(ERROR_CODE, "pipeline lock"))?
            .as_mut()
        {
            pipeline
                .renderer
                .lock()
                .map_err(|_| NativeError::at(ERROR_CODE, "renderer lock"))?
                .tint = tint;
        }
        Ok(())
    }

    pub fn update_geometry(&self, geometry: SamplingGeometry) -> Result<(), NativeError> {
        if !self.enabled() {
            return Ok(());
        }
        self.liquid_visual
            .SetOffset(Vector3 {
                X: geometry.input_surface.x as f32,
                Y: geometry.input_surface.y as f32,
                Z: 0.0,
            })
            .and_then(|_| {
                self.liquid_visual.SetSize(Vector2 {
                    X: geometry.input_surface.width as f32,
                    Y: geometry.input_surface.height as f32,
                })
            })
            .map_err(|error| NativeError::at("LIQUID_GLASS_VISUAL_GEOMETRY_FAILED", error))?;
        *self
            .latest_geometry
            .lock()
            .map_err(|_| NativeError::at(ERROR_CODE, "geometry lock"))? = Some(geometry);
        if !self
            .requested_visible
            .load(std::sync::atomic::Ordering::Acquire)
        {
            return Ok(());
        }
        self.ensure_pipeline(geometry)
    }

    fn ensure_pipeline(&self, geometry: SamplingGeometry) -> Result<(), NativeError> {
        let hwnd = HWND(self.hwnd_value as *mut std::ffi::c_void);
        let monitor = unsafe { MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST) };
        if monitor.0.is_null() {
            return self.fail(
                "LIQUID_GLASS_MONITOR_UNAVAILABLE",
                "MonitorFromWindow returned null",
            );
        }
        let mut slot = self
            .pipeline
            .lock()
            .map_err(|_| NativeError::at(ERROR_CODE, "pipeline lock"))?;
        let size = [geometry.input_surface.width, geometry.input_surface.height];
        let rebuild = slot.as_ref().is_none_or(|pipeline| {
            pipeline.monitor_value != monitor.0 as isize || pipeline.size != size
        });
        if rebuild {
            self.ready
                .store(false, std::sync::atomic::Ordering::Release);
            slot.take();
            let tint = *self
                .tint
                .lock()
                .map_err(|_| NativeError::at(ERROR_CODE, "tint lock"))?;
            match NativePipeline::create(
                hwnd,
                monitor,
                &self.compositor,
                &self.liquid_brush,
                &self.liquid_visual,
                &self.gaussian_visual,
                Arc::clone(&self.fused),
                Arc::clone(&self.requested_visible),
                Arc::clone(&self.ready),
                geometry,
                configured_debug_step(),
                tint,
            ) {
                Ok(next) => *slot = Some(next),
                Err(error) => {
                    drop(slot);
                    return self.fail(error.code, &error.detail);
                }
            }
        } else if let Some(pipeline) = slot.as_mut() {
            pipeline.update_geometry(geometry)?;
        }
        drop(slot);
        Ok(())
    }

    fn fail<T>(&self, code: &'static str, detail: &str) -> Result<T, NativeError> {
        self.fused.store(true, std::sync::atomic::Ordering::Release);
        self.ready
            .store(false, std::sync::atomic::Ordering::Release);
        if let Ok(mut pipeline) = self.pipeline.lock() {
            pipeline.take();
        }
        let _ = self.liquid_visual.SetIsVisible(false);
        let _ = self.gaussian_visual.SetIsVisible(true);
        Err(NativeError {
            code,
            detail: detail.to_owned(),
        })
    }
}

impl Drop for SinglePipelineController {
    fn drop(&mut self) {
        if let Ok(mut pipeline) = self.pipeline.lock() {
            pipeline.take();
        }
        let _ = self.children.Remove(&self.liquid_visual);
    }
}

struct NativePipeline {
    monitor_value: isize,
    size: [u32; 2],
    renderer: Arc<Mutex<Renderer>>,
    frame_pool: Direct3D11CaptureFramePool,
    frame_token: i64,
    session: GraphicsCaptureSession,
    session_slot: Arc<Mutex<Option<GraphicsCaptureSession>>>,
}

impl NativePipeline {
    #[allow(clippy::too_many_arguments)]
    fn create(
        hwnd: HWND,
        monitor: HMONITOR,
        compositor: &Compositor,
        brush: &CompositionSurfaceBrush,
        visual: &SpriteVisual,
        gaussian_visual: &SpriteVisual,
        fused: Arc<std::sync::atomic::AtomicBool>,
        requested_visible: Arc<std::sync::atomic::AtomicBool>,
        ready: Arc<std::sync::atomic::AtomicBool>,
        geometry: SamplingGeometry,
        debug_step: DebugStep,
        tint: [f32; 4],
    ) -> Result<Self, NativeError> {
        let (device, context) = create_device()?;
        let dxgi_device: IDXGIDevice = device
            .cast()
            .map_err(|error| NativeError::at("LIQUID_GLASS_DXGI_DEVICE_FAILED", error))?;
        let inspectable = unsafe { CreateDirect3D11DeviceFromDXGIDevice(&dxgi_device) }
            .map_err(|error| NativeError::at("LIQUID_GLASS_WINRT_DEVICE_FAILED", error))?;
        let winrt_device: IDirect3DDevice = inspectable
            .cast()
            .map_err(|error| NativeError::at("LIQUID_GLASS_WINRT_DEVICE_CAST_FAILED", error))?;
        let item_interop = factory::<GraphicsCaptureItem, IGraphicsCaptureItemInterop>()
            .map_err(|error| NativeError::at("LIQUID_GLASS_CAPTURE_INTEROP_FAILED", error))?;
        let item = unsafe { item_interop.CreateForMonitor::<GraphicsCaptureItem>(monitor) }
            .map_err(|error| NativeError::at("LIQUID_GLASS_CAPTURE_ITEM_FAILED", error))?;
        let monitor_size = item
            .Size()
            .map_err(|error| NativeError::at("LIQUID_GLASS_CAPTURE_SIZE_FAILED", error))?;
        if monitor_size.Width <= 0 || monitor_size.Height <= 0 {
            return Err(NativeError::at(
                "LIQUID_GLASS_CAPTURE_SIZE_INVALID",
                "empty monitor",
            ));
        }
        let renderer = Arc::new(Mutex::new(Renderer::create(
            hwnd,
            device,
            context,
            compositor,
            brush,
            geometry,
            [monitor_size.Width as u32, monitor_size.Height as u32],
            debug_step,
            tint,
        )?));
        let frame_pool = Direct3D11CaptureFramePool::CreateFreeThreaded(
            &winrt_device,
            DirectXPixelFormat::B8G8R8A8UIntNormalized,
            i32::from(MAX_CAPTURE_BUFFERS),
            monitor_size,
        )
        .map_err(|error| NativeError::at("LIQUID_GLASS_FRAME_POOL_FAILED", error))?;
        let busy = Arc::new(std::sync::atomic::AtomicBool::new(false));
        let session_slot: Arc<Mutex<Option<GraphicsCaptureSession>>> = Arc::new(Mutex::new(None));
        let renderer_for_frame = Arc::clone(&renderer);
        let busy_for_frame = Arc::clone(&busy);
        let visual_for_frame = visual.clone();
        let gaussian_for_frame = gaussian_visual.clone();
        let fused_for_frame = Arc::clone(&fused);
        let session_for_frame = Arc::clone(&session_slot);
        let frame_token = frame_pool
            .FrameArrived(
                &TypedEventHandler::<Direct3D11CaptureFramePool, IInspectable>::new(
                    move |pool, _| {
                        if fused_for_frame.load(std::sync::atomic::Ordering::Acquire)
                            || busy_for_frame
                                .compare_exchange(
                                    false,
                                    true,
                                    std::sync::atomic::Ordering::AcqRel,
                                    std::sync::atomic::Ordering::Acquire,
                                )
                                .is_err()
                        {
                            return Ok(());
                        }
                        let result = (|| -> windows::core::Result<()> {
                            let pool = pool.as_ref().ok_or_else(windows::core::Error::empty)?;
                            let frame = pool.TryGetNextFrame()?;
                            let surface = frame.Surface()?;
                            let access = surface.cast::<IDirect3DDxgiInterfaceAccess>()?;
                            let texture = unsafe { access.GetInterface::<ID3D11Texture2D>()? };
                            renderer_for_frame
                                .lock()
                                .map_err(|_| windows::core::Error::empty())?
                                .render(&texture)?;
                            frame.Close()?;
                            Ok(())
                        })();
                        busy_for_frame.store(false, std::sync::atomic::Ordering::Release);
                        if result.is_err() {
                            fused_for_frame.store(true, std::sync::atomic::Ordering::Release);
                            ready.store(false, std::sync::atomic::Ordering::Release);
                            let _ = visual_for_frame.SetIsVisible(false);
                            let _ = gaussian_for_frame.SetIsVisible(true);
                            if let Ok(mut slot) = session_for_frame.lock() {
                                if let Some(session) = slot.take() {
                                    let _ = session.Close();
                                }
                            }
                        } else {
                            ready.store(true, std::sync::atomic::Ordering::Release);
                            if requested_visible.load(std::sync::atomic::Ordering::Acquire) {
                                let _ = visual_for_frame.SetIsVisible(true);
                                let _ = gaussian_for_frame.SetIsVisible(false);
                            }
                        }
                        Ok(())
                    },
                ),
            )
            .map_err(|error| NativeError::at("LIQUID_GLASS_FRAME_HANDLER_FAILED", error))?;
        let session = frame_pool
            .CreateCaptureSession(&item)
            .map_err(|error| NativeError::at("LIQUID_GLASS_CAPTURE_SESSION_FAILED", error))?;
        let _ = session.SetIsCursorCaptureEnabled(false);
        let _ = session.SetIsBorderRequired(false);
        *session_slot
            .lock()
            .map_err(|_| NativeError::at(ERROR_CODE, "session lock"))? = Some(session.clone());
        session
            .StartCapture()
            .map_err(|error| NativeError::at("LIQUID_GLASS_CAPTURE_START_FAILED", error))?;
        Ok(Self {
            monitor_value: monitor.0 as isize,
            size: [geometry.input_surface.width, geometry.input_surface.height],
            renderer,
            frame_pool,
            frame_token,
            session,
            session_slot,
        })
    }

    fn update_geometry(&mut self, geometry: SamplingGeometry) -> Result<(), NativeError> {
        self.renderer
            .lock()
            .map_err(|_| NativeError::at(ERROR_CODE, "renderer lock"))?
            .geometry = geometry;
        Ok(())
    }
}

impl Drop for NativePipeline {
    fn drop(&mut self) {
        let _ = self.frame_pool.RemoveFrameArrived(self.frame_token);
        let _ = self.session.Close();
        if let Ok(mut slot) = self.session_slot.lock() {
            slot.take();
        }
        let _ = self.frame_pool.Close();
    }
}

#[repr(C)]
#[derive(Clone, Copy)]
struct BlurConstants {
    groups: [[f32; 4]; 3],
}

#[repr(C)]
#[derive(Clone, Copy)]
struct LiquidConstants {
    optics: [[f32; 4]; 6],
    // HLSL declares this register as `uint debugStep; float3 padding`.
    // Keep it integer-typed on the Rust side too instead of smuggling the bit
    // pattern through an f32; both sides still occupy one 16-byte register.
    debug: [u32; 4],
    sampling: [[f32; 4]; 3],
}

struct TargetTexture {
    _texture: ID3D11Texture2D,
    srv: ID3D11ShaderResourceView,
    rtv: ID3D11RenderTargetView,
}

struct Renderer {
    hwnd_value: isize,
    device: ID3D11Device,
    context: ID3D11DeviceContext,
    monitor_size: [u32; 2],
    geometry: SamplingGeometry,
    debug_step: DebugStep,
    tint: [f32; 4],
    vertex_shader: ID3D11VertexShader,
    blur_shader: ID3D11PixelShader,
    liquid_shader: ID3D11PixelShader,
    sampler: ID3D11SamplerState,
    blur_constants: ID3D11Buffer,
    liquid_constants: ID3D11Buffer,
    first_blur: TargetTexture,
    second_blur: TargetTexture,
    blur_size: [u32; 2],
    blur_padding: u32,
    swap_chain: IDXGISwapChain1,
    _surface_brush: CompositionSurfaceBrush,
}

impl Renderer {
    #[allow(clippy::too_many_arguments)]
    fn create(
        hwnd: HWND,
        device: ID3D11Device,
        context: ID3D11DeviceContext,
        compositor: &Compositor,
        brush: &CompositionSurfaceBrush,
        geometry: SamplingGeometry,
        monitor_size: [u32; 2],
        debug_step: DebugStep,
        tint: [f32; 4],
    ) -> Result<Self, NativeError> {
        let vertex_bytes = compile_shader(FULLSCREEN_VERTEX_HLSL, "vs_main", "vs_5_0")?;
        let blur_bytes = compile_shader(BLUR_PIXEL_HLSL, "ps_blur", "ps_5_0")?;
        let liquid_bytes = compile_shader(LIQUID_PIXEL_HLSL, "ps_liquid", "ps_5_0")?;
        let mut vertex_shader = None;
        let mut blur_shader = None;
        let mut liquid_shader = None;
        unsafe {
            device.CreateVertexShader(&vertex_bytes, None, Some(&mut vertex_shader))?;
            device.CreatePixelShader(&blur_bytes, None, Some(&mut blur_shader))?;
            device.CreatePixelShader(&liquid_bytes, None, Some(&mut liquid_shader))?;
        }
        let size = [geometry.input_surface.width, geometry.input_surface.height];
        // Both blur passes include four sigma of pixels around the input bar.
        // Without this gutter the second pass clamps at the bar boundary and
        // produces a visible hard strip, especially along the left edge.
        let blur_padding = (geometry.blur_sigma * 4.0).ceil().max(1.0) as u32;
        let blur_size = padded_size(size, blur_padding)?;
        let first_blur = create_target(&device, blur_size)?;
        let second_blur = create_target(&device, blur_size)?;
        let swap_chain = create_swap_chain(&device, size)?;
        let interop: ICompositorInterop = compositor
            .cast()
            .map_err(|error| NativeError::at("LIQUID_GLASS_COMPOSITOR_INTEROP_FAILED", error))?;
        let surface = unsafe { interop.CreateCompositionSurfaceForSwapChain(&swap_chain) }
            .map_err(|error| NativeError::at("LIQUID_GLASS_COMPOSITION_SURFACE_FAILED", error))?;
        brush
            .SetSurface(&surface)
            .map_err(|error| NativeError::at("LIQUID_GLASS_SURFACE_BIND_FAILED", error))?;
        let sampler = create_sampler(&device)?;
        let blur_constants = create_constant_buffer(&device, std::mem::size_of::<BlurConstants>())?;
        let liquid_constants =
            create_constant_buffer(&device, std::mem::size_of::<LiquidConstants>())?;
        Ok(Self {
            hwnd_value: hwnd.0 as isize,
            device,
            context,
            monitor_size,
            geometry,
            debug_step,
            tint,
            vertex_shader: vertex_shader
                .ok_or_else(|| NativeError::at(ERROR_CODE, "vertex shader missing"))?,
            blur_shader: blur_shader
                .ok_or_else(|| NativeError::at(ERROR_CODE, "blur shader missing"))?,
            liquid_shader: liquid_shader
                .ok_or_else(|| NativeError::at(ERROR_CODE, "liquid shader missing"))?,
            sampler,
            blur_constants,
            liquid_constants,
            first_blur,
            second_blur,
            blur_size,
            blur_padding,
            swap_chain,
            _surface_brush: brush.clone(),
        })
    }

    fn render(&mut self, source: &ID3D11Texture2D) -> windows::core::Result<()> {
        let mut source_srv = None;
        unsafe {
            self.device
                .CreateShaderResourceView(source, None, Some(&mut source_srv))?;
        }
        let source_srv = source_srv.ok_or_else(windows::core::Error::empty)?;
        let mapping = self.background_mapping()?;
        let size = [
            self.geometry.input_surface.width,
            self.geometry.input_surface.height,
        ];
        let blur_source_mapping = padded_background_mapping(
            mapping,
            self.monitor_size,
            self.blur_size,
            self.blur_padding,
        );
        let input_in_blur_mapping =
            input_in_padded_mapping(size, self.blur_size, self.blur_padding);
        self.draw_blur(
            &source_srv,
            &self.first_blur.rtv,
            [
                1.0 / self.monitor_size[0] as f32,
                1.0 / self.monitor_size[1] as f32,
            ],
            [0.0, 1.0],
            blur_source_mapping,
        )?;
        self.draw_blur(
            &self.first_blur.srv,
            &self.second_blur.rtv,
            [
                1.0 / self.blur_size[0] as f32,
                1.0 / self.blur_size[1] as f32,
            ],
            [1.0, 0.0],
            [0.0, 0.0, 1.0, 1.0],
        )?;
        let back_buffer: ID3D11Texture2D = unsafe { self.swap_chain.GetBuffer(0)? };
        let mut back_rtv = None;
        unsafe {
            self.device
                .CreateRenderTargetView(&back_buffer, None, Some(&mut back_rtv))?;
        }
        let back_rtv = back_rtv.ok_or_else(windows::core::Error::empty)?;
        let constants = self.liquid_values(mapping, input_in_blur_mapping);
        update_buffer(&self.context, &self.liquid_constants, &constants);
        self.bind_target(&back_rtv, size);
        unsafe {
            self.context.PSSetShader(&self.liquid_shader, None);
            self.context.PSSetShaderResources(
                0,
                Some(&[Some(source_srv), Some(self.second_blur.srv.clone())]),
            );
            self.context
                .PSSetConstantBuffers(0, Some(&[Some(self.liquid_constants.clone())]));
            self.context
                .ClearRenderTargetView(&back_rtv, &[0.0, 0.0, 0.0, 0.0]);
            self.context.Draw(3, 0);
            self.context.PSSetShaderResources(0, Some(&[None, None]));
            self.swap_chain.Present(1, DXGI_PRESENT(0)).ok()?;
        }
        Ok(())
    }

    fn draw_blur(
        &self,
        source: &ID3D11ShaderResourceView,
        target: &ID3D11RenderTargetView,
        texel: [f32; 2],
        direction: [f32; 2],
        mapping: [f32; 4],
    ) -> windows::core::Result<()> {
        let constants = BlurConstants {
            groups: [
                [texel[0], texel[1], direction[0], direction[1]],
                mapping,
                [self.geometry.blur_sigma, 0.0, 0.0, 0.0],
            ],
        };
        update_buffer(&self.context, &self.blur_constants, &constants);
        self.bind_target(target, self.blur_size);
        unsafe {
            self.context
                .ClearRenderTargetView(target, &[0.0, 0.0, 0.0, 0.0]);
            self.context.PSSetShader(&self.blur_shader, None);
            self.context
                .PSSetShaderResources(0, Some(&[Some(source.clone())]));
            self.context
                .PSSetConstantBuffers(0, Some(&[Some(self.blur_constants.clone())]));
            self.context.Draw(3, 0);
            self.context.PSSetShaderResources(0, Some(&[None]));
        }
        Ok(())
    }

    fn bind_target(&self, target: &ID3D11RenderTargetView, size: [u32; 2]) {
        unsafe {
            self.context
                .OMSetRenderTargets(Some(&[Some(target.clone())]), None);
            self.context.RSSetViewports(Some(&[D3D11_VIEWPORT {
                Width: size[0] as f32,
                Height: size[1] as f32,
                MinDepth: 0.0,
                MaxDepth: 1.0,
                ..Default::default()
            }]));
            self.context.IASetInputLayout(None);
            self.context
                .IASetPrimitiveTopology(D3D_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
            self.context.VSSetShader(&self.vertex_shader, None);
            self.context
                .PSSetSamplers(0, Some(&[Some(self.sampler.clone())]));
        }
    }

    fn background_mapping(&self) -> windows::core::Result<[f32; 4]> {
        let hwnd = HWND(self.hwnd_value as *mut std::ffi::c_void);
        let mut rect = RECT::default();
        unsafe { GetWindowRect(hwnd, &mut rect)? };
        let monitor = unsafe { MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST) };
        let mut info = MONITORINFO {
            cbSize: std::mem::size_of::<MONITORINFO>() as u32,
            ..Default::default()
        };
        unsafe { GetMonitorInfoW(monitor, &mut info).ok()? };
        let x = rect.left + self.geometry.input_surface.x - info.rcMonitor.left;
        let y = rect.top + self.geometry.input_surface.y - info.rcMonitor.top;
        Ok([
            x as f32 / self.monitor_size[0] as f32,
            y as f32 / self.monitor_size[1] as f32,
            self.geometry.input_surface.width as f32 / self.monitor_size[0] as f32,
            self.geometry.input_surface.height as f32 / self.monitor_size[1] as f32,
        ])
    }

    fn liquid_values(
        &self,
        background_mapping: [f32; 4],
        blurred_mapping: [f32; 4],
    ) -> LiquidConstants {
        let width = self.geometry.input_surface.width as f32;
        let height = self.geometry.input_surface.height as f32;
        LiquidConstants {
            optics: [
                [width, height, width * 0.5, height * 0.5],
                [
                    width,
                    height,
                    self.geometry.corner_radius,
                    REFRACTION_THICKNESS * self.geometry.effect_scale,
                ],
                [REFRACTION_FACTOR, DISPERSION, 0.0, 0.0],
                [FRESNEL_RANGE, FRESNEL_HARDNESS, FRESNEL_FACTOR, GLARE_RANGE],
                [
                    GLARE_ANGLE_RADIANS,
                    GLARE_FACTOR,
                    GLARE_OPPOSITE,
                    GLARE_CONVERGENCE,
                ],
                [GLARE_HARDNESS, 0.0, 0.0, 0.0],
            ],
            debug: [self.debug_step as u32, 0, 0, 0],
            sampling: [self.tint, background_mapping, blurred_mapping],
        }
    }
}

fn padded_size(size: [u32; 2], padding: u32) -> Result<[u32; 2], NativeError> {
    let doubled = padding
        .checked_mul(2)
        .ok_or_else(|| NativeError::at("LIQUID_GLASS_BLUR_SIZE_INVALID", "padding overflow"))?;
    Ok([
        size[0]
            .checked_add(doubled)
            .ok_or_else(|| NativeError::at("LIQUID_GLASS_BLUR_SIZE_INVALID", "width overflow"))?,
        size[1]
            .checked_add(doubled)
            .ok_or_else(|| NativeError::at("LIQUID_GLASS_BLUR_SIZE_INVALID", "height overflow"))?,
    ])
}

fn padded_background_mapping(
    input_mapping: [f32; 4],
    monitor_size: [u32; 2],
    blur_size: [u32; 2],
    padding: u32,
) -> [f32; 4] {
    [
        input_mapping[0] - padding as f32 / monitor_size[0] as f32,
        input_mapping[1] - padding as f32 / monitor_size[1] as f32,
        blur_size[0] as f32 / monitor_size[0] as f32,
        blur_size[1] as f32 / monitor_size[1] as f32,
    ]
}

fn input_in_padded_mapping(input_size: [u32; 2], blur_size: [u32; 2], padding: u32) -> [f32; 4] {
    [
        padding as f32 / blur_size[0] as f32,
        padding as f32 / blur_size[1] as f32,
        input_size[0] as f32 / blur_size[0] as f32,
        input_size[1] as f32 / blur_size[1] as f32,
    ]
}

fn create_device() -> Result<(ID3D11Device, ID3D11DeviceContext), NativeError> {
    let mut device = None;
    let mut context = None;
    unsafe {
        D3D11CreateDevice(
            None,
            D3D_DRIVER_TYPE_HARDWARE,
            HMODULE::default(),
            D3D11_CREATE_DEVICE_BGRA_SUPPORT,
            None,
            D3D11_SDK_VERSION,
            Some(&mut device),
            None,
            Some(&mut context),
        )
    }
    .map_err(|error| NativeError::at("LIQUID_GLASS_D3D_DEVICE_FAILED", error))?;
    Ok((
        device.ok_or_else(|| NativeError::at(ERROR_CODE, "device missing"))?,
        context.ok_or_else(|| NativeError::at(ERROR_CODE, "context missing"))?,
    ))
}

fn create_target(device: &ID3D11Device, size: [u32; 2]) -> Result<TargetTexture, NativeError> {
    let desc = D3D11_TEXTURE2D_DESC {
        Width: size[0],
        Height: size[1],
        MipLevels: 1,
        ArraySize: 1,
        Format: DXGI_FORMAT_B8G8R8A8_UNORM,
        SampleDesc: DXGI_SAMPLE_DESC {
            Count: 1,
            Quality: 0,
        },
        Usage: D3D11_USAGE_DEFAULT,
        BindFlags: (D3D11_BIND_RENDER_TARGET | D3D11_BIND_SHADER_RESOURCE).0 as u32,
        ..Default::default()
    };
    let mut texture = None;
    let mut srv = None;
    let mut rtv = None;
    unsafe {
        device.CreateTexture2D(&desc, None, Some(&mut texture))?;
        let texture_ref = texture.as_ref().ok_or_else(windows::core::Error::empty)?;
        device.CreateShaderResourceView(texture_ref, None, Some(&mut srv))?;
        device.CreateRenderTargetView(texture_ref, None, Some(&mut rtv))?;
    }
    Ok(TargetTexture {
        _texture: texture.ok_or_else(|| NativeError::at(ERROR_CODE, "target texture missing"))?,
        srv: srv.ok_or_else(|| NativeError::at(ERROR_CODE, "target srv missing"))?,
        rtv: rtv.ok_or_else(|| NativeError::at(ERROR_CODE, "target rtv missing"))?,
    })
}

fn create_swap_chain(
    device: &ID3D11Device,
    size: [u32; 2],
) -> Result<IDXGISwapChain1, NativeError> {
    let factory: IDXGIFactory2 = unsafe { CreateDXGIFactory2(DXGI_CREATE_FACTORY_FLAGS(0)) }
        .map_err(|error| NativeError::at("LIQUID_GLASS_DXGI_FACTORY_FAILED", error))?;
    let desc = DXGI_SWAP_CHAIN_DESC1 {
        Width: size[0],
        Height: size[1],
        Format: DXGI_FORMAT_B8G8R8A8_UNORM,
        SampleDesc: DXGI_SAMPLE_DESC {
            Count: 1,
            Quality: 0,
        },
        BufferUsage: DXGI_USAGE_RENDER_TARGET_OUTPUT,
        BufferCount: u32::from(MAX_SWAP_CHAIN_BUFFERS),
        Scaling: DXGI_SCALING_STRETCH,
        SwapEffect: DXGI_SWAP_EFFECT_FLIP_SEQUENTIAL,
        AlphaMode: DXGI_ALPHA_MODE_PREMULTIPLIED,
        ..Default::default()
    };
    unsafe { factory.CreateSwapChainForComposition(device, &desc, None) }
        .map_err(|error| NativeError::at("LIQUID_GLASS_SWAP_CHAIN_FAILED", error))
}

fn create_sampler(device: &ID3D11Device) -> Result<ID3D11SamplerState, NativeError> {
    let desc = D3D11_SAMPLER_DESC {
        Filter: D3D11_FILTER_MIN_MAG_MIP_LINEAR,
        AddressU: D3D11_TEXTURE_ADDRESS_CLAMP,
        AddressV: D3D11_TEXTURE_ADDRESS_CLAMP,
        AddressW: D3D11_TEXTURE_ADDRESS_CLAMP,
        ComparisonFunc: D3D11_COMPARISON_NEVER,
        MinLOD: 0.0,
        MaxLOD: f32::MAX,
        ..Default::default()
    };
    let mut sampler = None;
    unsafe { device.CreateSamplerState(&desc, Some(&mut sampler)) }
        .map_err(|error| NativeError::at("LIQUID_GLASS_SAMPLER_FAILED", error))?;
    sampler.ok_or_else(|| NativeError::at(ERROR_CODE, "sampler missing"))
}

fn create_constant_buffer(device: &ID3D11Device, size: usize) -> Result<ID3D11Buffer, NativeError> {
    let desc = D3D11_BUFFER_DESC {
        ByteWidth: size as u32,
        Usage: D3D11_USAGE_DEFAULT,
        BindFlags: D3D11_BIND_CONSTANT_BUFFER.0 as u32,
        ..Default::default()
    };
    let mut buffer = None;
    unsafe { device.CreateBuffer(&desc, None, Some(&mut buffer)) }
        .map_err(|error| NativeError::at("LIQUID_GLASS_CONSTANT_BUFFER_FAILED", error))?;
    buffer.ok_or_else(|| NativeError::at(ERROR_CODE, "constant buffer missing"))
}

fn update_buffer<T>(context: &ID3D11DeviceContext, buffer: &ID3D11Buffer, value: &T) {
    unsafe {
        context.UpdateSubresource(buffer, 0, None, value as *const T as *const _, 0, 0);
    }
}

fn compile_shader(source: &str, entry: &str, target: &str) -> Result<Vec<u8>, NativeError> {
    // Runtime compilation is isolated behind the explicit PoC flag. `fxc` static
    // compilation remains a required pre-launch gate; this call uses the in-box
    // compiler only when the owner explicitly enables the candidate.
    let mut entry_bytes = entry.as_bytes().to_vec();
    entry_bytes.push(0);
    let mut target_bytes = target.as_bytes().to_vec();
    target_bytes.push(0);
    let mut code = None;
    let mut errors = None;
    let result = unsafe {
        windows::Win32::Graphics::Direct3D::Fxc::D3DCompile(
            source.as_ptr().cast(),
            source.len(),
            windows::core::PCSTR::null(),
            None,
            None::<&windows::Win32::Graphics::Direct3D::ID3DInclude>,
            windows::core::PCSTR(entry_bytes.as_ptr()),
            windows::core::PCSTR(target_bytes.as_ptr()),
            0,
            0,
            &mut code,
            Some(&mut errors),
        )
    };
    if let Err(error) = result {
        let detail = errors.as_ref().map_or_else(
            || error.to_string(),
            |blob: &windows::Win32::Graphics::Direct3D::ID3DBlob| unsafe {
                let bytes = std::slice::from_raw_parts(
                    blob.GetBufferPointer().cast::<u8>(),
                    blob.GetBufferSize(),
                );
                String::from_utf8_lossy(bytes).into_owned()
            },
        );
        return Err(NativeError::at(
            "LIQUID_GLASS_SHADER_COMPILE_FAILED",
            detail,
        ));
    }
    let blob = code.ok_or_else(|| NativeError::at(ERROR_CODE, "compiled shader missing"))?;
    Ok(unsafe {
        std::slice::from_raw_parts(blob.GetBufferPointer().cast::<u8>(), blob.GetBufferSize())
    }
    .to_vec())
}
