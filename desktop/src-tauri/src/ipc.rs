use std::collections::BTreeSet;
use std::fmt;
use std::io::{Read, Write};

use serde_json::Value;

pub const PROTOCOL_VERSION: u64 = 1;
pub const MAX_FRAME_SIZE: usize = 8 * 1024 * 1024;
const HEADER_SIZE: usize = 4;
const MESSAGE_KINDS: [&str; 6] = [
    "request",
    "response",
    "event",
    "cancel",
    "stream_chunk",
    "stream_end",
];

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

pub fn validate_envelope(message: &Value) -> Result<(), IpcError> {
    let object = message
        .as_object()
        .ok_or_else(|| IpcError::new("INVALID_ENVELOPE", "message must be a JSON object"))?;
    if object.get("protocol").and_then(Value::as_u64) != Some(PROTOCOL_VERSION) {
        return Err(IpcError::new(
            "INVALID_ENVELOPE",
            "unsupported protocol version",
        ));
    }
    let kind = non_empty_string(object.get("kind"), "kind")?;
    if !MESSAGE_KINDS.contains(&kind) {
        return Err(IpcError::new("INVALID_ENVELOPE", "unknown message kind"));
    }
    non_empty_string(object.get("id"), "id")?;
    non_empty_string(object.get("session_id"), "session_id")?;
    if object.get("sequence").and_then(Value::as_u64).unwrap_or(0) < 1 {
        return Err(IpcError::new(
            "INVALID_ENVELOPE",
            "sequence must be a positive integer",
        ));
    }
    match kind {
        "request" => {
            non_empty_string(object.get("method"), "method")?;
        }
        "response" => {
            let ok = object.get("ok").and_then(Value::as_bool).ok_or_else(|| {
                IpcError::new("INVALID_ENVELOPE", "response must include boolean ok")
            })?;
            if !ok {
                let error = object
                    .get("error")
                    .and_then(Value::as_object)
                    .ok_or_else(|| {
                        IpcError::new("INVALID_ENVELOPE", "failed response must include error")
                    })?;
                non_empty_string(error.get("code"), "code")?;
                non_empty_string(error.get("message"), "message")?;
                if error.get("retryable").and_then(Value::as_bool).is_none() {
                    return Err(IpcError::new(
                        "INVALID_ENVELOPE",
                        "error retryable must be boolean",
                    ));
                }
                if error
                    .get("details")
                    .is_some_and(|details| !details.is_object())
                {
                    return Err(IpcError::new(
                        "INVALID_ENVELOPE",
                        "error details must be an object",
                    ));
                }
            }
        }
        "cancel" => {
            non_empty_string(object.get("target_id"), "target_id")?;
        }
        _ => {}
    }
    Ok(())
}

fn non_empty_string<'a>(value: Option<&'a Value>, key: &str) -> Result<&'a str, IpcError> {
    let text = value.and_then(Value::as_str).unwrap_or_default();
    if text.trim().is_empty() {
        return Err(IpcError::new(
            "INVALID_ENVELOPE",
            format!("{key} must be a non-empty string"),
        ));
    }
    Ok(text)
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

#[derive(Debug, Default)]
pub struct FrameDecoder {
    buffer: Vec<u8>,
    expected_length: Option<usize>,
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
                    .expect("header slice has fixed size");
                self.buffer.drain(..HEADER_SIZE);
                let length = u32::from_be_bytes(header) as usize;
                if length > MAX_FRAME_SIZE {
                    self.buffer.clear();
                    return Err(IpcError::new(
                        "FRAME_TOO_LARGE",
                        format!("frame payload exceeds {MAX_FRAME_SIZE} bytes"),
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
            let payload: Vec<u8> = self.buffer.drain(..expected).collect();
            self.expected_length = None;
            messages.push(decode_payload(&payload)?);
        }
        Ok(messages)
    }

    pub fn finish(&self) -> Result<(), IpcError> {
        if self.expected_length.is_some() || !self.buffer.is_empty() {
            return Err(IpcError::new(
                "INCOMPLETE_FRAME",
                "stream ended in the middle of a frame",
            ));
        }
        Ok(())
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
                    "stream ended in the middle of a frame header",
                ));
            }
            Ok(count) => read += count,
            Err(error) => return Err(IpcError::new("TRANSPORT_READ_FAILED", error.to_string())),
        }
    }
    let length = u32::from_be_bytes(header) as usize;
    if length > MAX_FRAME_SIZE {
        return Err(IpcError::new(
            "FRAME_TOO_LARGE",
            format!("frame payload exceeds {MAX_FRAME_SIZE} bytes"),
        ));
    }
    let mut payload = vec![0_u8; length];
    reader
        .read_exact(&mut payload)
        .map_err(|_| IpcError::new("INCOMPLETE_FRAME", "missing frame payload"))?;
    decode_payload(&payload).map(Some)
}

pub fn write_frame<W: Write>(writer: &mut W, message: &Value) -> Result<(), IpcError> {
    writer
        .write_all(&encode_frame(message)?)
        .and_then(|_| writer.flush())
        .map_err(|error| IpcError::new("TRANSPORT_WRITE_FAILED", error.to_string()))
}

pub fn error_response(
    request_id: &str,
    session_id: &str,
    sequence: u64,
    code: &str,
    message: &str,
    retryable: bool,
    details: Value,
) -> Value {
    serde_json::json!({
        "protocol": PROTOCOL_VERSION,
        "kind": "response",
        "id": request_id,
        "session_id": session_id,
        "sequence": sequence,
        "ok": false,
        "error": {
            "code": code,
            "message": message,
            "retryable": retryable,
            "details": details,
        }
    })
}

#[derive(Debug)]
pub struct SessionTracker {
    session_id: String,
    last_sequence: u64,
    seen_request_ids: BTreeSet<String>,
    pending_request_ids: BTreeSet<String>,
    closed: bool,
}

impl SessionTracker {
    pub fn new(session_id: impl Into<String>) -> Self {
        Self {
            session_id: session_id.into(),
            last_sequence: 0,
            seen_request_ids: BTreeSet::new(),
            pending_request_ids: BTreeSet::new(),
            closed: false,
        }
    }

    pub fn accept(&mut self, message: &Value) -> Result<(), IpcError> {
        if self.closed {
            return Err(IpcError::new("SESSION_CLOSED", "IPC session is closed"));
        }
        validate_envelope(message)?;
        if message["session_id"].as_str() != Some(self.session_id.as_str()) {
            return Err(IpcError::new(
                "SESSION_MISMATCH",
                "message belongs to another session",
            ));
        }
        let sequence = message["sequence"]
            .as_u64()
            .expect("validated sequence is u64");
        let expected = self.last_sequence + 1;
        if sequence != expected {
            return Err(IpcError::new(
                "INVALID_SEQUENCE",
                format!("expected sequence {expected}, got {sequence}"),
            ));
        }
        if message["kind"] == "request" {
            let id = message["id"]
                .as_str()
                .expect("validated request ID is a string");
            if self.seen_request_ids.contains(id) {
                return Err(IpcError::new(
                    "DUPLICATE_REQUEST_ID",
                    "request ID was already used",
                ));
            }
            self.seen_request_ids.insert(id.to_string());
            self.pending_request_ids.insert(id.to_string());
        }
        self.last_sequence = sequence;
        Ok(())
    }

    pub fn complete(&mut self, request_id: &str) -> bool {
        self.pending_request_ids.remove(request_id)
    }

    pub fn close(&mut self) -> Vec<String> {
        let terminated = self.pending_request_ids.iter().cloned().collect();
        self.pending_request_ids.clear();
        self.closed = true;
        terminated
    }
}

#[cfg(test)]
fn hex_encode(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

#[cfg(test)]
mod tests {
    use std::fs;

    use serde::Deserialize;
    use serde_json::Value;

    use super::*;

    #[derive(Deserialize)]
    struct GoldenFixture {
        message: Value,
        frame_hex: String,
    }

    fn fixture() -> GoldenFixture {
        let path = concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../tests/fixtures/brain_host_frame_v1.json"
        );
        serde_json::from_str(&fs::read_to_string(path).expect("fixture should exist"))
            .expect("fixture should parse")
    }

    fn request(id: &str, sequence: u64) -> Value {
        serde_json::json!({
            "protocol": 1,
            "kind": "request",
            "id": id,
            "session_id": "session-1",
            "sequence": sequence,
            "method": "system.health",
            "deadline_ms": 30_000,
            "payload": {}
        })
    }

    #[test]
    fn rust_codec_matches_shared_golden_frame() {
        let fixture = fixture();
        let encoded = encode_frame(&fixture.message).expect("frame should encode");

        assert_eq!(hex_encode(&encoded), fixture.frame_hex);
        assert_eq!(
            decode_frame(&encoded).expect("frame should decode"),
            fixture.message
        );
    }

    #[test]
    fn decoder_accepts_fragmented_and_coalesced_frames() {
        let first = encode_frame(&request("req-1", 1)).unwrap();
        let second = encode_frame(&request("req-2", 2)).unwrap();
        let wire = [first, second].concat();
        let mut decoder = FrameDecoder::default();
        let mut decoded = Vec::new();

        for chunk in wire.chunks(3) {
            decoded.extend(decoder.feed(chunk).expect("chunk should decode"));
        }
        decoder.finish().expect("decoder should be complete");

        assert_eq!(decoded[0]["id"], "req-1");
        assert_eq!(decoded[1]["id"], "req-2");
    }

    #[test]
    fn rejects_oversized_and_invalid_json_frames() {
        let oversized = ((MAX_FRAME_SIZE + 1) as u32).to_be_bytes();
        let error = FrameDecoder::default().feed(&oversized).unwrap_err();
        assert_eq!(error.code, "FRAME_TOO_LARGE");

        let payload = b"{not-json}";
        let frame = [
            (payload.len() as u32).to_be_bytes().as_slice(),
            payload.as_slice(),
        ]
        .concat();
        let error = decode_frame(&frame).unwrap_err();
        assert_eq!(error.code, "INVALID_JSON");

        let invalid_utf8 = [1_u32.to_be_bytes().as_slice(), &[0xff]].concat();
        assert_eq!(
            decode_frame(&invalid_utf8).unwrap_err().code,
            "INVALID_UTF8"
        );
    }

    #[test]
    fn session_rejects_duplicate_ids_and_sequence_gaps() {
        let mut tracker = SessionTracker::new("session-1");
        tracker.accept(&request("req-1", 1)).unwrap();

        assert_eq!(
            tracker.accept(&request("req-1", 2)).unwrap_err().code,
            "DUPLICATE_REQUEST_ID"
        );
        assert_eq!(
            tracker.accept(&request("req-2", 3)).unwrap_err().code,
            "INVALID_SEQUENCE"
        );
    }

    #[test]
    fn closing_session_terminates_pending_requests() {
        let mut tracker = SessionTracker::new("session-1");
        tracker.accept(&request("req-1", 1)).unwrap();
        tracker.accept(&request("req-2", 2)).unwrap();

        assert_eq!(tracker.close(), vec!["req-1", "req-2"]);
        assert_eq!(
            tracker.accept(&request("req-3", 3)).unwrap_err().code,
            "SESSION_CLOSED"
        );
    }

    #[test]
    fn creates_stable_error_response() {
        let response = error_response(
            "req-1",
            "session-1",
            2,
            "BACKEND_UNAVAILABLE",
            "Brain is unavailable",
            true,
            serde_json::json!({"state": "starting"}),
        );

        validate_envelope(&response).unwrap();
        assert_eq!(response["error"]["code"], "BACKEND_UNAVAILABLE");
        assert_eq!(response["error"]["retryable"], true);
        assert_eq!(response["error"]["details"]["state"], "starting");
    }
}
