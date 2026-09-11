//! Native PNG alpha-mask service used by renderers, without manifest semantics.
use std::{fs::File, io::{Cursor, Read}, path::Path};
use serde::Serialize;
pub use crate::visual_resources::*;

#[derive(Clone, Copy, Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct PortraitMetadata {
    pub width: u32,
    pub height: u32,
    pub byte_length: u64,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PortraitAlphaMask {
    pub width: u32,
    pub height: u32,
    pub alpha: Vec<u8>,
    visible_bounds: Option<[u32; 4]>,
}

impl PortraitAlphaMask {
    pub fn new(width: u32, height: u32, alpha: Vec<u8>) -> Self {
        let expected_len = usize::try_from(u64::from(width) * u64::from(height)).ok();
        let visible_bounds = (width > 0 && height > 0 && expected_len == Some(alpha.len()))
            .then(|| {
                let mut bounds: Option<(u32, u32, u32, u32)> = None;
                for (index, value) in alpha.iter().copied().enumerate() {
                    if value == 0 {
                        continue;
                    }
                    let x = index as u32 % width;
                    let y = index as u32 / width;
                    bounds = Some(match bounds {
                        None => (x, y, x, y),
                        Some((left, top, right, bottom)) => {
                            (left.min(x), top.min(y), right.max(x), bottom.max(y))
                        }
                    });
                }
                bounds.map(|(left, top, right, bottom)| {
                    [left, top, right - left + 1, bottom - top + 1]
                })
            })
            .flatten();
        Self {
            width,
            height,
            alpha,
            visible_bounds,
        }
    }

    pub fn source_size(&self) -> [u32; 2] {
        [self.width, self.height]
    }

    pub fn visible_bounds(&self) -> Option<[u32; 4]> {
        self.visible_bounds
    }
}

pub(crate) fn inspect_png(path: &Path, byte_length: u64) -> Result<PortraitMetadata, String> {
    let mut header = [0_u8; 33];
    File::open(path)
        .and_then(|mut file| file.read_exact(&mut header))
        .map_err(|_| "CHARACTER_RESOURCE_DECODE_REJECTED".to_string())?;
    png_metadata(&header, byte_length)
}

fn png_metadata(bytes: &[u8], byte_length: u64) -> Result<PortraitMetadata, String> {
    const SIGNATURE: [u8; 8] = [137, 80, 78, 71, 13, 10, 26, 10];
    if bytes.len() < 33
        || bytes[..8] != SIGNATURE
        || bytes[8..12] != [0, 0, 0, 13]
        || bytes[12..16] != *b"IHDR"
    {
        return Err("CHARACTER_RESOURCE_MIME_REJECTED".to_string());
    }
    let width = u32::from_be_bytes(bytes[16..20].try_into().expect("PNG width slice"));
    let height = u32::from_be_bytes(bytes[20..24].try_into().expect("PNG height slice"));
    if width == 0
        || height == 0
        || width > 8192
        || height > 8192
        || u64::from(width) * u64::from(height) > 40_000_000
    {
        return Err("CHARACTER_RESOURCE_DIMENSIONS_REJECTED".to_string());
    }
    Ok(PortraitMetadata {
        width,
        height,
        byte_length,
    })
}

pub(crate) fn decode_png_alpha_mask(
    bytes: &[u8],
    expected: PortraitMetadata,
) -> Result<PortraitAlphaMask, String> {
    const MAX_DECODE_BYTES: usize = 192 * 1024 * 1024;
    let mut decoder = png::Decoder::new_with_limits(
        Cursor::new(bytes),
        png::Limits {
            bytes: MAX_DECODE_BYTES,
        },
    );
    decoder.set_transformations(png::Transformations::ALPHA | png::Transformations::STRIP_16);
    let mut reader = decoder
        .read_info()
        .map_err(|_| "CHARACTER_RESOURCE_DECODE_REJECTED".to_string())?;
    let output_size = reader
        .output_buffer_size()
        .filter(|size| *size <= MAX_DECODE_BYTES)
        .ok_or_else(|| "CHARACTER_RESOURCE_DECODE_REJECTED".to_string())?;
    let mut decoded = vec![0_u8; output_size];
    let info = reader
        .next_frame(&mut decoded)
        .map_err(|_| "CHARACTER_RESOURCE_DECODE_REJECTED".to_string())?;
    if info.width != expected.width
        || info.height != expected.height
        || info.bit_depth != png::BitDepth::Eight
        || !matches!(
            info.color_type,
            png::ColorType::GrayscaleAlpha | png::ColorType::Rgba
        )
    {
        return Err("CHARACTER_RESOURCE_DECODE_REJECTED".to_string());
    }
    let channels = info.color_type.samples();
    let pixel_count = usize::try_from(u64::from(info.width) * u64::from(info.height))
        .map_err(|_| "CHARACTER_RESOURCE_DECODE_REJECTED".to_string())?;
    let frame = &decoded[..info.buffer_size()];
    if frame.len() != pixel_count.saturating_mul(channels) {
        return Err("CHARACTER_RESOURCE_DECODE_REJECTED".to_string());
    }
    let alpha = frame
        .chunks_exact(channels)
        .map(|pixel| pixel[channels - 1])
        .collect();
    Ok(PortraitAlphaMask::new(info.width, info.height, alpha))
}
