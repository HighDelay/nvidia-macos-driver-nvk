use metal2vulkan::passes::{Stage, TransformOptions};
use metal2vulkan::{disassemble, reflect_sanitized, translate_sanitized_native};

fn shader(body: &str, kind: &str, declarations: &str) -> String {
    format!(
        r#"target triple = "air64_v28-apple-macosx26.7.0"
%Group = type {{ ptr addrspace(1) }}
define <4 x float> @fs(ptr addrspace(2) %group) {{
entry:
  %field = getelementptr inbounds %Group, ptr addrspace(2) %group, i64 0, i32 0
  %tex = load ptr addrspace(1), ptr addrspace(2) %field, align 8
{body}
}}
{declarations}
!air.fragment = !{{!0}}
!0 = !{{ptr @fs, !1, !2}}
!1 = !{{!3}}
!3 = !{{!"air.render_target", i32 0, i32 0, !"air.arg_type_name", !"float4"}}
!2 = !{{!4}}
!4 = !{{i32 0, !"air.indirect_buffer", !"air.buffer_size", i32 8, !"air.location_index", i32 0, i32 1, !"air.read", !"air.address_space", i32 2, !"air.struct_type_info", !5, !"air.arg_type_name", !"Group", !"air.arg_name", !"group"}}
!5 = !{{i32 0, i32 8, i32 0, !"{kind}", !"tex", !"air.indirect_argument", !6}}
!6 = !{{i32 0, !"air.texture", !"air.location_index", i32 0, i32 1, !"air.read", !"air.arg_type_name", !"{kind}", !"air.arg_name", !"tex"}}
"#
    )
}

fn assembly(ll: &str) -> String {
    let dir = std::env::temp_dir().join(format!("m2v_arg_image_query_{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let spv = translate_sanitized_native(ll, Stage::Fragment, &dir).expect("translate");
    disassemble(&spv).expect("disassemble")
}

#[test]
fn query_only_argument_texture_keeps_dimensions_and_mip_levels() {
    let ll = shader(
        r#"  %width = call i32 @air.get_width_texture_2d(ptr addrspace(1) %tex, i32 2)
  %levels = call i32 @air.get_num_mip_levels_texture_2d(ptr addrspace(1) %tex)
  %wf = uitofp i32 %width to float
  %lf = uitofp i32 %levels to float
  %v0 = insertelement <4 x float> zeroinitializer, float %wf, i32 0
  %v1 = insertelement <4 x float> %v0, float %lf, i32 1
  ret <4 x float> %v1"#,
        "texture2d<float, read>",
        "declare i32 @air.get_width_texture_2d(ptr addrspace(1), i32)\ndeclare i32 @air.get_num_mip_levels_texture_2d(ptr addrspace(1))",
    );
    let asm = assembly(&ll);
    assert!(asm.contains("OpImageQuerySizeLod"), "{asm}");
    assert!(asm.contains("OpImageQueryLevels"), "{asm}");
}

#[test]
fn query_only_multisample_argument_texture_keeps_size_and_sample_count() {
    for kind in [
        "texture2d_ms<float, read>",
        "texture2d_ms_array<uint, read>",
    ] {
        let suffix = if kind.contains("array") {
            "2d_ms_array"
        } else {
            "2d_ms"
        };
        let ll = shader(
            &format!(r#"  %width = call i32 @air.get_width_texture_{suffix}(ptr addrspace(1) %tex)
  %samples = call i32 @air.get_num_samples_texture_{suffix}(ptr addrspace(1) %tex)
  %wf = uitofp i32 %width to float
  %sf = uitofp i32 %samples to float
  %v0 = insertelement <4 x float> zeroinitializer, float %wf, i32 0
  %v1 = insertelement <4 x float> %v0, float %sf, i32 1
  ret <4 x float> %v1"#), kind,
            &format!("declare i32 @air.get_width_texture_{suffix}(ptr addrspace(1))\ndeclare i32 @air.get_num_samples_texture_{suffix}(ptr addrspace(1))"),
        );
        let asm = assembly(&ll);
        assert!(asm.contains("OpImageQuerySize "), "{asm}");
        assert!(asm.contains("OpImageQuerySamples"), "{asm}");
        assert!(!asm.contains("OpImageQuerySizeLod"), "{asm}");
        let reflection =
            reflect_sanitized(&ll, Stage::Fragment, TransformOptions::default()).unwrap();
        let shape = reflection
            .bindings
            .iter()
            .find_map(|b| b.texture_shape.as_ref())
            .unwrap();
        assert!(shape.multisampled);
        assert_eq!(shape.arrayed, kind.contains("array"));
    }
}

#[test]
fn four_sample_argument_texture_resolve_fetches_samples_instead_of_mip_levels() {
    let mut body = String::new();
    for i in 0..4 {
        body.push_str(&format!("  %s{i} = call {{ <4 x float>, i8 }} @air.read_texture_2d_ms.v4f32(ptr addrspace(1) %tex, <2 x i32> zeroinitializer, i32 {i}, i32 1)\n  %c{i} = extractvalue {{ <4 x float>, i8 }} %s{i}, 0\n"));
    }
    body.push_str("  %a = fadd <4 x float> %c0, %c1\n  %b = fadd <4 x float> %c2, %c3\n  %sum = fadd <4 x float> %a, %b\n  %mean = fmul <4 x float> %sum, <float 0.25, float 0.25, float 0.25, float 0.25>\n  ret <4 x float> %mean");
    let ll = shader(&body, "texture2d_ms<float, read>", "declare { <4 x float>, i8 } @air.read_texture_2d_ms.v4f32(ptr addrspace(1), <2 x i32>, i32, i32)");
    let asm = assembly(&ll);
    let fetches: Vec<_> = asm
        .lines()
        .filter(|line| line.contains("OpImageFetch"))
        .collect();
    assert_eq!(fetches.len(), 4, "{asm}");
    assert!(
        fetches
            .iter()
            .all(|line| line.contains(" Sample ") && !line.contains(" Lod ")),
        "{asm}"
    );
}
