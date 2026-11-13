use metal2vulkan::passes::Stage;
use metal2vulkan::{disassemble, translate_sanitized_native};
use std::collections::{HashMap, HashSet};

// Reduced from Metal's optimized copy of two texture2d members. The second
// handle is loaded from the wrapper itself, through an opaque-pointer bitcast.
// The uniform pointer forces raw argument-buffer lowering, as in WebGPU.
const SHADER: &str = r#"target triple = "air64_v28-apple-macosx26.7.0"
%Args = type { ptr addrspace(2), %"struct.metal::texture2d", %"struct.metal::texture2d" }
%"struct.metal::texture2d" = type { ptr addrspace(1) }

define <4 x float> @fs(ptr addrspace(2) %args, ptr addrspace(2) %smp) {
entry:
  %uf = getelementptr inbounds %Args, ptr addrspace(2) %args, i64 0, i32 0
  %uniform = load ptr addrspace(2), ptr addrspace(2) %uf, align 8
  %offset = load <4 x float>, ptr addrspace(2) %uniform, align 16
  %field0 = getelementptr inbounds %Args, ptr addrspace(2) %args, i64 0, i32 1, i32 0
  %tex0 = load ptr addrspace(1), ptr addrspace(2) %field0, align 8
  %field1 = getelementptr inbounds %Args, ptr addrspace(2) %args, i64 0, i32 2
  %bytes1 = bitcast ptr addrspace(2) %field1 to ptr addrspace(2)
  %raw1 = load i64, ptr addrspace(2) %bytes1, align 8
  %tex1 = inttoptr i64 %raw1 to ptr addrspace(1)
  %s0 = call { <4 x float>, i8 } @air.sample_texture_2d.v4f32(ptr addrspace(1) %tex0, ptr addrspace(2) %smp, <2 x float> zeroinitializer, i1 true, <2 x i32> zeroinitializer, i1 false, float 0.0, float 0.0, i32 0)
  %s1 = call { <4 x float>, i8 } @air.sample_texture_2d.v4f32(ptr addrspace(1) %tex1, ptr addrspace(2) %smp, <2 x float> zeroinitializer, i1 true, <2 x i32> zeroinitializer, i1 false, float 0.0, float 0.0, i32 0)
  %c0 = extractvalue { <4 x float>, i8 } %s0, 0
  %c1 = extractvalue { <4 x float>, i8 } %s1, 0
  %textures = fadd <4 x float> %c0, %c1
  %sum = fadd <4 x float> %textures, %offset
  ret <4 x float> %sum
}
declare { <4 x float>, i8 } @air.sample_texture_2d.v4f32(ptr addrspace(1), ptr addrspace(2), <2 x float>, i1, <2 x i32>, i1, float, float, i32)

!air.fragment = !{!0}
!0 = !{ptr @fs, !1, !2}
!1 = !{!3}
!3 = !{!"air.render_target", i32 0, i32 0, !"air.arg_type_name", !"float4"}
!2 = !{!4, !8}
!4 = !{i32 0, !"air.indirect_buffer", !"air.buffer_size", i32 24, !"air.location_index", i32 1, i32 1, !"air.read", !"air.address_space", i32 2, !"air.struct_type_info", !5, !"air.arg_type_name", !"Args", !"air.arg_name", !"args"}
!5 = !{i32 0, i32 8, i32 0, !"float4*", !"uniform", !"air.indirect_argument", !9, i32 8, i32 8, i32 0, !"texture2d<float, sample>", !"t0", !"air.indirect_argument", !6, i32 16, i32 8, i32 0, !"texture2d<float, sample>", !"t1", !"air.indirect_argument", !7}
!6 = !{i32 0, !"air.texture", !"air.location_index", i32 3, i32 1, !"air.sample", !"air.arg_type_name", !"texture2d<float, sample>", !"air.arg_name", !"t0"}
!7 = !{i32 1, !"air.texture", !"air.location_index", i32 7, i32 1, !"air.sample", !"air.arg_type_name", !"texture2d<float, sample>", !"air.arg_name", !"t1"}
!8 = !{i32 1, !"air.sampler", !"air.location_index", i32 0, i32 1, !"air.arg_type_name", !"sampler", !"air.arg_name", !"smp"}
!9 = !{i32 2, !"air.buffer", !"air.location_index", i32 2, i32 1, !"air.read", !"air.address_space", i32 2, !"air.arg_type_size", i32 16, !"air.arg_type_align_size", i32 16, !"air.arg_type_name", !"float4*", !"air.arg_name", !"uniform"}
"#;

fn translate(ll: &str) -> Result<Vec<u8>, String> {
    let dir = std::env::temp_dir().join(format!("m2v_integer_resource_{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    translate_sanitized_native(ll, Stage::Fragment, &dir)
}

fn sampled_texture_offsets(ll: &str) -> Vec<u32> {
    let spv = translate(ll).expect("translate the actual shader without a fallback");
    let asm = disassemble(&spv).expect("disassemble");
    let mut definitions = HashMap::new();
    let mut bindings = HashMap::new();
    let mut constants = HashMap::new();
    for line in asm.lines() {
        let fields: Vec<_> = line.split_whitespace().collect();
        if fields.first() == Some(&"OpDecorate") && fields.get(2) == Some(&"Binding") {
            bindings.insert(fields[1], fields[3].parse::<u32>().unwrap());
        }
        if fields.get(1) == Some(&"=") {
            definitions.insert(fields[0], fields[2..].to_vec());
            if fields.get(2) == Some(&"OpConstant") {
                constants.insert(fields[0], fields[4].parse::<u32>().unwrap());
            }
        }
    }
    let mut sampled = Vec::new();
    for definition in definitions.values() {
        if definition.first() != Some(&"OpSampledImage") {
            continue;
        }
        let load = &definitions[definition[2]];
        assert_eq!(
            load[0], "OpLoad",
            "the sampled texture is a descriptor load:\n{asm}"
        );
        let image_slot = &definitions[load[2]];
        assert_eq!(image_slot[0], "OpAccessChain", "{asm}");
        assert_eq!(
            bindings[image_slot[2]], 0,
            "the texture heap is bound: {asm}"
        );
        let masked_slot = &definitions[image_slot[3]];
        assert_eq!(masked_slot[0], "OpBitwiseAnd", "{asm}");
        let handle_word = &definitions[masked_slot[2]];
        assert_eq!(handle_word[0], "OpLoad", "{asm}");
        let field = &definitions[handle_word[2]];
        assert_eq!(field[0], "OpInBoundsAccessChain", "{asm}");
        assert_eq!(bindings[field[2]], 1, "the argument buffer is bound: {asm}");
        sampled.push(4 * constants[field[4]]);
    }
    sampled.sort_unstable();
    assert_eq!(sampled.len(), 2, "both texture samples remain:\n{asm}");
    assert_eq!(
        sampled.iter().collect::<HashSet<_>>().len(),
        2,
        "the two texture fields retain distinct resource payloads:\n{asm}"
    );
    sampled
}

#[test]
fn integer_copied_texture_keeps_the_same_bindings_as_a_pointer_load() {
    let canonical = SHADER.replace(
        "  %raw1 = load i64, ptr addrspace(2) %bytes1, align 8\n  %tex1 = inttoptr i64 %raw1 to ptr addrspace(1)",
        "  %tex1 = load ptr addrspace(1), ptr addrspace(2) %bytes1, align 8",
    );
    assert_eq!(sampled_texture_offsets(SHADER), vec![8, 16]);
    assert_eq!(
        sampled_texture_offsets(SHADER),
        sampled_texture_offsets(&canonical)
    );
}

#[test]
fn an_integer_load_of_the_inner_pointer_also_keeps_its_binding() {
    let inner = SHADER.replace("i64 0, i32 2\n", "i64 0, i32 2, i32 0\n");
    assert_eq!(
        sampled_texture_offsets(&inner),
        sampled_texture_offsets(SHADER)
    );
}

#[test]
fn a_texture_only_group_keeps_integer_handles_without_device_addressing() {
    let ll = SHADER
        .replace(
            "  %uniform = load ptr addrspace(2), ptr addrspace(2) %uf, align 8\n",
            "",
        )
        .replace(
            "  %offset = load <4 x float>, ptr addrspace(2) %uniform, align 16\n",
            "",
        )
        .replace("  %sum = fadd <4 x float> %textures, %offset\n", "")
        .replace("ret <4 x float> %sum", "ret <4 x float> %textures");
    let asm = disassemble(&translate(&ll).expect("translate without nested uniform pointers"))
        .expect("disassemble");
    assert!(!asm.contains("PhysicalStorageBuffer64"), "{asm}");
    assert_eq!(sampled_texture_offsets(&ll), vec![8, 16]);
}

#[test]
fn the_sampler_retry_retains_nested_uniform_buffer_addresses() {
    // The real retry reads its samplers from an argument group, rather than
    // receiving an already loaded sampler as a direct function parameter.
    let ll = SHADER
        .replace(
            "%Args = type",
            "%Samplers = type { ptr addrspace(2) }\n%Args = type",
        )
        .replace("ptr addrspace(2) %smp) {", "ptr addrspace(2) %samplers) {")
        .replace(
            "entry:\n",
            "entry:\n  %sf = getelementptr inbounds %Samplers, ptr addrspace(2) %samplers, i64 0, i32 0\n  %smp = load ptr addrspace(2), ptr addrspace(2) %sf, align 8\n",
        )
        .replace(
            "!8 = !{i32 1, !\"air.sampler\", !\"air.location_index\", i32 0, i32 1, !\"air.arg_type_name\", !\"sampler\", !\"air.arg_name\", !\"smp\"}",
            "!8 = !{i32 1, !\"air.indirect_buffer\", !\"air.buffer_size\", i32 8, !\"air.location_index\", i32 0, i32 1, !\"air.read\", !\"air.address_space\", i32 2, !\"air.struct_type_info\", !10, !\"air.arg_type_name\", !\"Samplers\", !\"air.arg_name\", !\"samplers\"}",
        )
        + r#"
!10 = !{i32 0, i32 8, i32 0, !"sampler", !"smp", !"air.indirect_argument", !11}
!11 = !{i32 0, !"air.sampler", !"air.location_index", i32 0, i32 1, !"air.arg_type_name", !"sampler", !"air.arg_name", !"smp"}
"#;
    let spv = metal2vulkan::with_argument_buffer_samplers(|| translate(&ll))
        .expect("translate the raw-first sampler retry");
    let asm = disassemble(&spv).expect("disassemble");
    assert!(
        asm.contains("PhysicalStorageBuffer64") && asm.contains("OpConvertUToPtr"),
        "the nested uniform is dereferenced instead of replaced with zero:\n{asm}"
    );
}

#[test]
fn separate_uniform_and_texture_groups_keep_both_texture_samples() {
    let split = SHADER
        .replace(
            "%Args = type { ptr addrspace(2),",
            "%Uniforms = type { ptr addrspace(2) }\n%Args = type {",
        )
        .replace(
            "ptr addrspace(2) %smp) {",
            "ptr addrspace(2) %smp, ptr addrspace(2) %uniforms) {",
        )
        .replace(
            "%uf = getelementptr inbounds %Args, ptr addrspace(2) %args",
            "%uf = getelementptr inbounds %Uniforms, ptr addrspace(2) %uniforms",
        )
        .replace("i64 0, i32 1, i32 0\n", "i64 0, i32 0, i32 0\n")
        .replace("i64 0, i32 2\n", "i64 0, i32 1\n")
        .replace("!2 = !{!4, !8}", "!2 = !{!4, !8, !10}")
        .replace(
            "!\"air.buffer_size\", i32 24",
            "!\"air.buffer_size\", i32 16",
        )
        .replace(
            "i32 0, i32 8, i32 0, !\"float4*\", !\"uniform\", !\"air.indirect_argument\", !9, ",
            "",
        )
        .replace(
            "i32 8, i32 8, i32 0, !\"texture2d<float, sample>\", !\"t0\"",
            "i32 0, i32 8, i32 0, !\"texture2d<float, sample>\", !\"t0\"",
        )
        .replace(
            "i32 16, i32 8, i32 0, !\"texture2d<float, sample>\", !\"t1\"",
            "i32 8, i32 8, i32 0, !\"texture2d<float, sample>\", !\"t1\"",
        );
    let split = split
        + r#"
!10 = !{i32 2, !"air.indirect_buffer", !"air.buffer_size", i32 8, !"air.location_index", i32 0, i32 1, !"air.read", !"air.address_space", i32 2, !"air.struct_type_info", !11, !"air.arg_type_name", !"Uniforms", !"air.arg_name", !"uniforms"}
!11 = !{i32 0, i32 8, i32 0, !"float4*", !"uniform", !"air.indirect_argument", !9}
"#;
    assert_eq!(sampled_texture_offsets(&split), vec![0, 8]);
}

#[test]
fn an_observed_integer_payload_is_not_silently_replaced_with_a_texture() {
    let observed = SHADER
        .replace("  %tex1 = inttoptr", "  %isnull = icmp eq i64 %raw1, 0\n  %tex1 = inttoptr")
        .replace("  ret <4 x float> %sum", "  %result = select i1 %isnull, <4 x float> zeroinitializer, <4 x float> %sum\n  ret <4 x float> %result");
    let error = translate(&observed)
        .expect_err("an additionally observed payload still requires integer semantics");
    assert!(error.contains("unknown SSA value %tex1"), "{error}");
}
