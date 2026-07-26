use std::{
    fmt,
    io::{Read, Write},
};

use serde_json::{Map, Value};

pub const PROTOCOL_MAJOR: u64 = 2;
/// Latest wire minor.  Minor 2 adds the generation-scoped event envelope;
/// 2.0/2.1 request/response lifecycle messages remain valid unchanged.
pub const PROTOCOL_MINOR: u64 = 2;
pub const EVENT_PROTOCOL_MINOR: u64 = 2;
pub const MAX_FRAME_SIZE: usize = 8 * 1024 * 1024;
const HEADER_SIZE: usize = 4;
const MESSAGE_KINDS: [&str; 3] = ["request", "response", "event"];
const PRIORITIES: [&str; 3] = ["control", "interactive", "background"];

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct IpcError {
    pub code: &'static str,
    pub message: String,
}

impl IpcError {
    fn new(code: &'static str, message: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into(),
        }
    }
}

impl fmt::Display for IpcError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(formatter, "{}: {}", self.code, self.message)
    }
}

impl std::error::Error for IpcError {}

fn object(message: &Value) -> Result<&Map<String, Value>, IpcError> {
    message
        .as_object()
        .ok_or_else(|| IpcError::new("INVALID_ENVELOPE", "message must be a JSON object"))
}

fn non_empty_string<'a>(message: &'a Map<String, Value>, key: &str) -> Result<&'a str, IpcError> {
    let value = message.get(key).and_then(Value::as_str).unwrap_or_default();
    if value.trim().is_empty() {
        Err(IpcError::new(
            "INVALID_ENVELOPE",
            format!("{key} must be a non-empty string"),
        ))
    } else {
        Ok(value)
    }
}

fn non_negative_integer(message: &Map<String, Value>, key: &str) -> Result<u64, IpcError> {
    message.get(key).and_then(Value::as_u64).ok_or_else(|| {
        IpcError::new(
            "INVALID_ENVELOPE",
            format!("{key} must be a non-negative integer"),
        )
    })
}

pub fn validate_envelope(message: &Value) -> Result<(), IpcError> {
    let message = object(message)?;
    non_negative_integer(message, "protocolMajor")?;
    non_negative_integer(message, "protocolMinor")?;
    let kind = non_empty_string(message, "kind")?;
    if !MESSAGE_KINDS.contains(&kind) {
        return Err(IpcError::new("INVALID_ENVELOPE", "unknown message kind"));
    }
    non_empty_string(message, "generationId")?;
    if kind == "response" || kind == "event" || message.contains_key("generationCredential") {
        non_empty_string(message, "generationCredential")?;
    }
    non_empty_string(message, "id")?;
    non_empty_string(message, "name")?;
    if !message.get("payload").is_some_and(Value::is_object) {
        return Err(IpcError::new(
            "INVALID_ENVELOPE",
            "payload must be an object",
        ));
    }

    if kind == "request" {
        if non_negative_integer(message, "deadlineMs")? == 0 {
            return Err(IpcError::new(
                "INVALID_ENVELOPE",
                "deadlineMs must be positive",
            ));
        }
        let priority = non_empty_string(message, "priority")?;
        if !PRIORITIES.contains(&priority) {
            return Err(IpcError::new("INVALID_ENVELOPE", "unknown priority"));
        }
    } else if kind == "response" {
        let ok = message
            .get("ok")
            .and_then(Value::as_bool)
            .ok_or_else(|| IpcError::new("INVALID_ENVELOPE", "response must include boolean ok"))?;
        if !ok {
            let error = message
                .get("error")
                .and_then(Value::as_object)
                .ok_or_else(|| {
                    IpcError::new("INVALID_ENVELOPE", "failed response must include error")
                })?;
            non_empty_string(error, "code")?;
            non_empty_string(error, "message")?;
            if error.get("retryable").and_then(Value::as_bool).is_none() {
                return Err(IpcError::new(
                    "INVALID_ENVELOPE",
                    "error retryable must be boolean",
                ));
            }
            if error.get("details").is_some_and(|value| !value.is_object()) {
                return Err(IpcError::new(
                    "INVALID_ENVELOPE",
                    "error details must be an object",
                ));
            }
        }
    } else {
        let minor = non_negative_integer(message, "protocolMinor")?;
        if minor < EVENT_PROTOCOL_MINOR {
            return Err(IpcError::new(
                "INVALID_ENVELOPE",
                "event requires protocol minor 2.2",
            ));
        }
        for forbidden in ["deadlineMs", "priority", "ok", "error"] {
            if message.contains_key(forbidden) {
                return Err(IpcError::new(
                    "INVALID_ENVELOPE",
                    format!("event must not include {forbidden}"),
                ));
            }
        }
    }
    Ok(())
}

pub fn encode_frame(message: &Value) -> Result<Vec<u8>, IpcError> {
    validate_envelope(message)?;
    let payload = serde_json::to_vec(message)
        .map_err(|error| IpcError::new("INVALID_JSON", error.to_string()))?;
    if payload.len() > MAX_FRAME_SIZE {
        return Err(IpcError::new(
            "FRAME_TOO_LARGE",
            format!("frame payload exceeds {MAX_FRAME_SIZE} bytes"),
        ));
    }
    let mut frame = Vec::with_capacity(HEADER_SIZE + payload.len());
    frame.extend_from_slice(&(payload.len() as u32).to_be_bytes());
    frame.extend_from_slice(&payload);
    Ok(frame)
}

fn decode_payload(payload: &[u8]) -> Result<Value, IpcError> {
    std::str::from_utf8(payload)
        .map_err(|_| IpcError::new("INVALID_UTF8", "frame payload is not valid UTF-8"))?;
    let message: Value = serde_json::from_slice(payload)
        .map_err(|_| IpcError::new("INVALID_JSON", "frame payload is not valid JSON"))?;
    validate_envelope(&message)?;
    Ok(message)
}

#[derive(Debug)]
pub struct FrameDecoder {
    buffer: Vec<u8>,
    expected_length: Option<usize>,
    max_frame_size: usize,
}

impl Default for FrameDecoder {
    fn default() -> Self {
        Self {
            buffer: Vec::new(),
            expected_length: None,
            max_frame_size: MAX_FRAME_SIZE,
        }
    }
}

impl FrameDecoder {
    pub fn feed(&mut self, data: &[u8]) -> Result<Vec<Value>, IpcError> {
        self.buffer.extend_from_slice(data);
        let mut messages = Vec::new();
        loop {
            if self.expected_length.is_none() {
                if self.buffer.len() < HEADER_SIZE {
                    break;
                }
                let header: [u8; HEADER_SIZE] = self.buffer[..HEADER_SIZE]
                    .try_into()
                    .expect("header has fixed size");
                self.buffer.drain(..HEADER_SIZE);
                let length = u32::from_be_bytes(header) as usize;
                if length == 0 {
                    self.buffer.clear();
                    return Err(IpcError::new(
                        "INVALID_FRAME",
                        "frame payload must not be empty",
                    ));
                }
                if length > self.max_frame_size {
                    self.buffer.clear();
                    let code = if header.iter().all(u8::is_ascii_graphic) {
                        "STDOUT_FRAMING_POLLUTION"
                    } else {
                        "FRAME_TOO_LARGE"
                    };
                    return Err(IpcError::new(
                        code,
                        format!("frame payload exceeds {} bytes", self.max_frame_size),
                    ));
                }
                self.expected_length = Some(length);
            }
            let Some(expected) = self.expected_length else {
                break;
            };
            if self.buffer.len() < expected {
                break;
            }
            let payload = self.buffer.drain(..expected).collect::<Vec<_>>();
            self.expected_length = None;
            messages.push(decode_payload(&payload)?);
        }
        Ok(messages)
    }

    pub fn finish(&self) -> Result<(), IpcError> {
        if self.expected_length.is_some() || !self.buffer.is_empty() {
            Err(IpcError::new(
                "INCOMPLETE_FRAME",
                "stream ended in the middle of a frame",
            ))
        } else {
            Ok(())
        }
    }
}

pub fn decode_frame(frame: &[u8]) -> Result<Value, IpcError> {
    let mut decoder = FrameDecoder::default();
    let messages = decoder.feed(frame)?;
    decoder.finish()?;
    if messages.len() != 1 {
        return Err(IpcError::new(
            "INVALID_FRAME_COUNT",
            "expected exactly one frame",
        ));
    }
    Ok(messages.into_iter().next().expect("one message exists"))
}

pub fn read_frame<R: Read>(reader: &mut R) -> Result<Option<Value>, IpcError> {
    let mut header = [0_u8; HEADER_SIZE];
    let mut read = 0;
    while read < HEADER_SIZE {
        match reader.read(&mut header[read..]) {
            Ok(0) if read == 0 => return Ok(None),
            Ok(0) => {
                return Err(IpcError::new(
                    "INCOMPLETE_FRAME",
                    "stream ended in the middle of a frame",
                ));
            }
            Ok(count) => read += count,
            Err(error) => return Err(IpcError::new("TRANSPORT_READ_FAILED", error.to_string())),
        }
    }
    let length = u32::from_be_bytes(header) as usize;
    if length == 0 {
        return Err(IpcError::new(
            "INVALID_FRAME",
            "frame payload must not be empty",
        ));
    }
    if length > MAX_FRAME_SIZE {
        let code = if header.iter().all(u8::is_ascii_graphic) {
            "STDOUT_FRAMING_POLLUTION"
        } else {
            "FRAME_TOO_LARGE"
        };
        return Err(IpcError::new(
            code,
            format!("frame payload exceeds {MAX_FRAME_SIZE} bytes"),
        ));
    }
    let mut payload = vec![0_u8; length];
    reader.read_exact(&mut payload).map_err(|error| {
        if error.kind() == std::io::ErrorKind::UnexpectedEof {
            IpcError::new("INCOMPLETE_FRAME", "missing frame payload")
        } else {
            IpcError::new("TRANSPORT_READ_FAILED", "pipe read failed")
        }
    })?;
    decode_payload(&payload).map(Some)
}

pub fn write_frame<W: Write>(writer: &mut W, message: &Value) -> Result<(), IpcError> {
    writer
        .write_all(&encode_frame(message)?)
        .and_then(|_| writer.flush())
        .map_err(|error| IpcError::new("TRANSPORT_WRITE_FAILED", error.to_string()))
}

#[cfg(test)]
mod tests {
    use std::io::Cursor;

    use serde_json::{json, Value};

    use super::{decode_frame, encode_frame, read_frame, FrameDecoder, MAX_FRAME_SIZE};

    const GENERATION_ID: &str = "00000000-0000-4000-8000-000000001c01";

    fn request(id: &str, name: &str) -> Value {
        json!({
            "protocolMajor": 2,
            "protocolMinor": 0,
            "kind": "request",
            "generationId": GENERATION_ID,
            "generationCredential": "11111111111111111111111111111111",
            "id": id,
            "name": name,
            "payload": {},
            "deadlineMs": 3000,
            "priority": "control"
        })
    }

    #[test]
    fn codec_accepts_every_split_and_merged_frames() {
        let first = encode_frame(&request("one", "system.hello")).expect("first frame");
        let second = encode_frame(&request("two", "system.health")).expect("second frame");
        for split in 0..=first.len() {
            let mut decoder = FrameDecoder::default();
            let mut messages = decoder.feed(&first[..split]).expect("prefix should parse");
            messages.extend(decoder.feed(&first[split..]).expect("suffix should parse"));
            assert_eq!(messages, vec![request("one", "system.hello")]);
            decoder.finish().expect("complete frame should finish");
        }

        let mut decoder = FrameDecoder::default();
        let merged = [first, second].concat();
        assert_eq!(
            decoder.feed(&merged).expect("merged frames should parse"),
            vec![
                request("one", "system.hello"),
                request("two", "system.health")
            ]
        );
        decoder.finish().expect("merged frames should finish");
    }

    #[test]
    fn malformed_oversized_and_polluted_frames_fail_with_stable_codes() {
        let cases = [
            ([vec![0, 0, 0, 1], vec![0xff]].concat(), "INVALID_UTF8"),
            ([vec![0, 0, 0, 1], vec![b'{']].concat(), "INVALID_JSON"),
            (vec![0, 0, 0, 0], "INVALID_FRAME"),
            (
                ((MAX_FRAME_SIZE as u32) + 1).to_be_bytes().to_vec(),
                "FRAME_TOO_LARGE",
            ),
            (b"stdout pollution".to_vec(), "STDOUT_FRAMING_POLLUTION"),
        ];
        for (frame, expected_code) in cases {
            let error = decode_frame(&frame).expect_err("malformed frame must fail");
            assert_eq!(error.code, expected_code);
        }
    }

    #[test]
    fn incomplete_header_payload_and_clean_eof_are_distinct() {
        for partial in [
            vec![0],
            vec![0, 0, 0],
            [10_u32.to_be_bytes().to_vec(), b"{}".to_vec()].concat(),
        ] {
            let mut decoder = FrameDecoder::default();
            assert!(decoder.feed(&partial).expect("partial feed").is_empty());
            assert_eq!(
                decoder.finish().expect_err("partial stream must fail").code,
                "INCOMPLETE_FRAME"
            );
        }

        assert_eq!(
            read_frame(&mut Cursor::new(Vec::<u8>::new())).unwrap(),
            None
        );
        assert_eq!(
            read_frame(&mut Cursor::new(vec![0, 0]))
                .expect_err("partial header must fail")
                .code,
            "INCOMPLETE_FRAME"
        );
    }

    #[test]
    fn envelope_validation_rejects_boolean_deadline_and_oversized_payload() {
        let mut missing_payload = request("missing-payload", "system.hello");
        missing_payload
            .as_object_mut()
            .expect("request is an object")
            .remove("payload");
        assert_eq!(
            encode_frame(&missing_payload)
                .expect_err("missing payload must fail")
                .code,
            "INVALID_ENVELOPE"
        );

        let mut invalid = request("bad", "system.hello");
        invalid["deadlineMs"] = json!(true);
        assert_eq!(
            encode_frame(&invalid)
                .expect_err("boolean deadline must fail")
                .code,
            "INVALID_ENVELOPE"
        );

        let mut oversized = request("large", "system.hello");
        oversized["payload"] = json!({ "value": "x".repeat(MAX_FRAME_SIZE) });
        assert_eq!(
            encode_frame(&oversized)
                .expect_err("oversized payload must fail")
                .code,
            "FRAME_TOO_LARGE"
        );
    }

    #[test]
    fn protocol_22_event_is_distinct_from_21_request_response() {
        let message = json!({
            "protocolMajor": 2,
            "protocolMinor": 2,
            "kind": "event",
            "generationId": GENERATION_ID,
            "generationCredential": "11111111111111111111111111111111",
            "id": "event-source",
            "name": "fixture.completed",
            "payload": {"state": "completed"}
        });
        assert_eq!(
            decode_frame(&encode_frame(&message).unwrap()).unwrap(),
            message
        );

        for (key, value) in [
            ("protocolMinor", json!(1)),
            ("ok", json!(true)),
            ("deadlineMs", json!(3000)),
            ("priority", json!("interactive")),
        ] {
            let mut invalid = message.clone();
            invalid[key] = value;
            assert_eq!(
                encode_frame(&invalid)
                    .expect_err("invalid event must fail")
                    .code,
                "INVALID_ENVELOPE"
            );
        }

        let mut legacy = request("legacy-health", "system.health");
        legacy["protocolMinor"] = json!(1);
        assert_eq!(
            decode_frame(&encode_frame(&legacy).unwrap()).unwrap(),
            legacy
        );
    }
}
