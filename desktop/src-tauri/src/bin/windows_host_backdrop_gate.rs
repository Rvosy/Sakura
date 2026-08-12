#[cfg(not(windows))]
fn main() {
    eprintln!("windows-host-backdrop-gate is only available on Windows");
}

#[cfg(windows)]
fn main() -> windows::core::Result<()> {
    gate::run()
}

#[cfg(windows)]
mod gate {
    use std::{ffi::c_void, mem::size_of, sync::Mutex};

    use windows::{
        core::{implement, w, Error, Interface, Result, BOOL, HRESULT, HSTRING},
        Foundation::{IPropertyValue, PropertyValue},
        Graphics::Effects::{
            IGraphicsEffect, IGraphicsEffectSource, IGraphicsEffectSource_Impl,
            IGraphicsEffect_Impl,
        },
        Win32::{
            Foundation::{HINSTANCE, HWND, LPARAM, LRESULT, WPARAM},
            Graphics::{
                Direct2D::{
                    CLSID_D2D1GaussianBlur, Common::D2D1_BORDER_MODE_HARD,
                    D2D1_GAUSSIANBLUR_OPTIMIZATION_BALANCED,
                },
                Dwm::{DwmSetWindowAttribute, DWMWA_USE_HOSTBACKDROPBRUSH},
            },
            System::{
                LibraryLoader::GetModuleHandleW,
                WinRT::{
                    Composition::ICompositorDesktopInterop,
                    CreateDispatcherQueueController, DispatcherQueueOptions,
                    Graphics::Direct2D::{
                        IGraphicsEffectD2D1Interop, IGraphicsEffectD2D1Interop_Impl,
                        GRAPHICS_EFFECT_PROPERTY_MAPPING,
                    },
                    DQTAT_COM_ASTA, DQTYPE_THREAD_CURRENT,
                },
            },
            UI::WindowsAndMessaging::{
                CreateWindowExW, DefWindowProcW, DispatchMessageW, GetMessageW, LoadCursorW,
                PostQuitMessage, RegisterClassW, ShowWindow, TranslateMessage, CS_HREDRAW,
                CS_VREDRAW, CW_USEDEFAULT, IDC_ARROW, MSG, SW_SHOW, WM_DESTROY, WM_ERASEBKGND,
                WNDCLASSW, WS_EX_NOREDIRECTIONBITMAP, WS_OVERLAPPEDWINDOW,
            },
        },
        UI::{Color, Composition::Compositor},
    };
    use windows_numerics::Vector2;

    const CLASS_NAME: windows::core::PCWSTR = w!("SakuraHostBackdropGate");
    const WINDOW_TITLE: windows::core::PCWSTR =
        w!("Sakura Gate 2 - HostBackdrop + Gaussian 18px (drag over windows; close to finish)");
    const BLUR_STANDARD_DEVIATION: f32 = 18.0;
    const E_INVALIDARG_HRESULT: HRESULT = HRESULT(0x80070057_u32 as i32);

    pub fn run() -> Result<()> {
        unsafe {
            let instance = GetModuleHandleW(None)?;
            register_window_class(HINSTANCE(instance.0))?;
            let hwnd = CreateWindowExW(
                WS_EX_NOREDIRECTIONBITMAP,
                CLASS_NAME,
                WINDOW_TITLE,
                WS_OVERLAPPEDWINDOW,
                CW_USEDEFAULT,
                CW_USEDEFAULT,
                760,
                460,
                None,
                None,
                Some(HINSTANCE(instance.0)),
                None,
            )?;

            enable_host_backdrop(hwnd)?;
            let composition = CompositionState::install(hwnd)?;
            let _ = ShowWindow(hwnd, SW_SHOW);

            eprintln!(
                "[host-backdrop-gate] active hwnd={:?}; DWM flag enabled; Gaussian stddev={BLUR_STANDARD_DEVIATION}; no Tauri/WebView2/Core",
                hwnd.0
            );
            eprintln!(
                "[host-backdrop-gate] expected: live desktop with a faint pink tint; unexpected: black/solid/frozen content"
            );

            let mut message = MSG::default();
            while GetMessageW(&mut message, None, 0, 0).as_bool() {
                let _ = TranslateMessage(&message);
                DispatchMessageW(&message);
            }

            drop(composition);
        }
        Ok(())
    }

    unsafe fn register_window_class(instance: HINSTANCE) -> Result<()> {
        let class = WNDCLASSW {
            style: CS_HREDRAW | CS_VREDRAW,
            lpfnWndProc: Some(window_proc),
            hInstance: instance,
            hCursor: LoadCursorW(None, IDC_ARROW)?,
            lpszClassName: CLASS_NAME,
            ..Default::default()
        };
        if RegisterClassW(&class) == 0 {
            return Err(windows::core::Error::from_win32());
        }
        Ok(())
    }

    unsafe fn enable_host_backdrop(hwnd: HWND) -> Result<()> {
        let enabled = BOOL::from(true);
        DwmSetWindowAttribute(
            hwnd,
            DWMWA_USE_HOSTBACKDROPBRUSH,
            (&enabled as *const BOOL).cast::<c_void>(),
            size_of::<BOOL>() as u32,
        )
    }

    struct CompositionState {
        _dispatcher: windows::System::DispatcherQueueController,
        _compositor: Compositor,
        _target: windows::UI::Composition::Desktop::DesktopWindowTarget,
        _root: windows::UI::Composition::ContainerVisual,
        _backdrop_visual: windows::UI::Composition::SpriteVisual,
        _backdrop_brush: windows::UI::Composition::CompositionBackdropBrush,
        _blur_effect: IGraphicsEffect,
        _blur_factory: windows::UI::Composition::CompositionEffectFactory,
        _blur_brush: windows::UI::Composition::CompositionEffectBrush,
        _tint_visual: windows::UI::Composition::SpriteVisual,
        _tint_brush: windows::UI::Composition::CompositionColorBrush,
    }

    impl CompositionState {
        unsafe fn install(hwnd: HWND) -> Result<Self> {
            let dispatcher = CreateDispatcherQueueController(DispatcherQueueOptions {
                dwSize: size_of::<DispatcherQueueOptions>() as u32,
                threadType: DQTYPE_THREAD_CURRENT,
                apartmentType: DQTAT_COM_ASTA,
            })
            .map_err(|error| at("dispatcher_queue", error))?;
            let compositor = Compositor::new().map_err(|error| at("compositor", error))?;
            let interop: ICompositorDesktopInterop = compositor
                .cast()
                .map_err(|error| at("compositor_desktop_interop", error))?;
            let target = interop
                .CreateDesktopWindowTarget(hwnd, false)
                .map_err(|error| at("desktop_window_target", error))?;

            let fill = Vector2 { X: 1.0, Y: 1.0 };
            let root = compositor.CreateContainerVisual()?;
            root.SetRelativeSizeAdjustment(fill)?;

            let backdrop_brush = compositor.CreateHostBackdropBrush()?;
            let blur_source = windows::UI::Composition::CompositionEffectSourceParameter::Create(
                &HSTRING::from("backdrop"),
            )
            .map_err(|error| at("effect_source_parameter", error))?;
            let blur_source_interface = blur_source
                .cast()
                .map_err(|error| at("effect_source_parameter_cast", error))?;
            let blur_effect: IGraphicsEffect =
                GaussianBlurEffectDescription::new(BLUR_STANDARD_DEVIATION, blur_source_interface)
                    .into();
            blur_effect
                .cast::<IGraphicsEffectSource>()
                .map_err(|error| at("custom_effect_source_cast", error))?;
            blur_effect
                .cast::<IGraphicsEffectD2D1Interop>()
                .map_err(|error| at("custom_effect_d2d_interop_cast", error))?;
            let blur_factory = compositor
                .CreateEffectFactory(&blur_effect)
                .map_err(|error| at("gaussian_effect_factory", error))?;
            let blur_brush = blur_factory
                .CreateBrush()
                .map_err(|error| at("gaussian_effect_brush", error))?;
            blur_brush
                .SetSourceParameter(&HSTRING::from("backdrop"), &backdrop_brush)
                .map_err(|error| at("gaussian_source_binding", error))?;
            let backdrop_visual = compositor.CreateSpriteVisual()?;
            backdrop_visual.SetRelativeSizeAdjustment(fill)?;
            backdrop_visual.SetBrush(&blur_brush)?;
            root.Children()?.InsertAtBottom(&backdrop_visual)?;

            // The tint makes the test window boundary obvious while leaving desktop details visible.
            let tint_brush = compositor.CreateColorBrushWithColor(Color {
                A: 42,
                R: 255,
                G: 176,
                B: 214,
            })?;
            let tint_visual = compositor.CreateSpriteVisual()?;
            tint_visual.SetRelativeSizeAdjustment(fill)?;
            tint_visual.SetBrush(&tint_brush)?;
            root.Children()?.InsertAtTop(&tint_visual)?;

            target.SetRoot(&root)?;

            Ok(Self {
                _dispatcher: dispatcher,
                _compositor: compositor,
                _target: target,
                _root: root,
                _backdrop_visual: backdrop_visual,
                _backdrop_brush: backdrop_brush,
                _blur_effect: blur_effect,
                _blur_factory: blur_factory,
                _blur_brush: blur_brush,
                _tint_visual: tint_visual,
                _tint_brush: tint_brush,
            })
        }
    }

    fn at(step: &str, error: Error) -> Error {
        eprintln!(
            "[host-backdrop-gate] step={step} code={:?} message={error}",
            error.code()
        );
        Error::new(error.code(), format!("{step}: {error}"))
    }

    #[implement(IGraphicsEffect, IGraphicsEffectSource, IGraphicsEffectD2D1Interop)]
    struct GaussianBlurEffectDescription {
        name: Mutex<HSTRING>,
        standard_deviation: f32,
        source: IGraphicsEffectSource,
    }

    impl GaussianBlurEffectDescription {
        fn new(standard_deviation: f32, source: IGraphicsEffectSource) -> Self {
            Self {
                name: Mutex::new(HSTRING::from("SakuraGaussianBlur")),
                standard_deviation,
                source,
            }
        }
    }

    impl IGraphicsEffectSource_Impl for GaussianBlurEffectDescription_Impl {}

    impl IGraphicsEffect_Impl for GaussianBlurEffectDescription_Impl {
        fn Name(&self) -> Result<HSTRING> {
            self.name
                .lock()
                .map(|name| name.clone())
                .map_err(|_| Error::new(E_INVALIDARG_HRESULT, "blur effect name lock poisoned"))
        }

        fn SetName(&self, name: &HSTRING) -> Result<()> {
            *self.name.lock().map_err(|_| {
                Error::new(E_INVALIDARG_HRESULT, "blur effect name lock poisoned")
            })? = name.clone();
            Ok(())
        }
    }

    impl IGraphicsEffectD2D1Interop_Impl for GaussianBlurEffectDescription_Impl {
        fn GetEffectId(&self) -> Result<windows::core::GUID> {
            Ok(CLSID_D2D1GaussianBlur)
        }

        fn GetNamedPropertyMapping(
            &self,
            _name: &windows::core::PCWSTR,
            _index: *mut u32,
            _mapping: *mut GRAPHICS_EFFECT_PROPERTY_MAPPING,
        ) -> Result<()> {
            Err(E_INVALIDARG_HRESULT.into())
        }

        fn GetPropertyCount(&self) -> Result<u32> {
            Ok(3)
        }

        fn GetProperty(&self, index: u32) -> Result<IPropertyValue> {
            let value = match index {
                0 => PropertyValue::CreateSingle(self.standard_deviation)?,
                1 => PropertyValue::CreateUInt32(D2D1_GAUSSIANBLUR_OPTIMIZATION_BALANCED.0 as u32)?,
                2 => PropertyValue::CreateUInt32(D2D1_BORDER_MODE_HARD.0 as u32)?,
                _ => return Err(E_INVALIDARG_HRESULT.into()),
            };
            value.cast()
        }

        fn GetSource(&self, index: u32) -> Result<IGraphicsEffectSource> {
            if index == 0 {
                Ok(self.source.clone())
            } else {
                Err(E_INVALIDARG_HRESULT.into())
            }
        }

        fn GetSourceCount(&self) -> Result<u32> {
            Ok(1)
        }
    }

    unsafe extern "system" fn window_proc(
        hwnd: HWND,
        message: u32,
        wparam: WPARAM,
        lparam: LPARAM,
    ) -> LRESULT {
        match message {
            WM_ERASEBKGND => LRESULT(1),
            WM_DESTROY => {
                PostQuitMessage(0);
                LRESULT(0)
            }
            _ => DefWindowProcW(hwnd, message, wparam, lparam),
        }
    }

    #[cfg(test)]
    mod tests {
        use super::*;

        #[test]
        fn host_backdrop_attribute_uses_a_win32_bool_payload() {
            assert_eq!(DWMWA_USE_HOSTBACKDROPBRUSH.0, 17);
            assert_eq!(size_of::<BOOL>(), 4);
        }

        #[test]
        fn gate_window_avoids_a_redirection_surface() {
            assert_ne!(WS_EX_NOREDIRECTIONBITMAP.0, 0);
        }

        #[test]
        fn gaussian_gate_uses_an_obvious_but_bounded_blur_radius() {
            assert!((1.0..=30.0).contains(&BLUR_STANDARD_DEVIATION));
        }
    }
}
