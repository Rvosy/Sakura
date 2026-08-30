fn main() {
    println!("cargo:rerun-if-changed=../../../desktop/src-tauri/icons/icon.icns");
    println!("cargo:rerun-if-changed=../../../desktop/src-tauri/icons/icon.ico");
    println!("cargo:rerun-if-changed=../../../desktop/src-tauri/icons/icon.png");
    tauri_build::build();
}
