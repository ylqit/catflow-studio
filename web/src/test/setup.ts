import { config } from "@vue/test-utils";
import { defineComponent } from "vue";

const ElementContainerStub = defineComponent({
  inheritAttrs: false,
  props: { title: { type: String, default: "" } },
  template: "<div v-bind=\"$attrs\"><span v-if=\"title\">{{ title }}</span><slot /><slot name=\"footer\" /><slot name=\"actions\" /></div>",
});

const ElementModelContainerStub = defineComponent({
  inheritAttrs: false,
  props: { modelValue: { type: [String, Number, Boolean, Object, Array], default: undefined } },
  emits: ["update:modelValue"],
  template: "<div v-if=\"modelValue !== false\" v-bind=\"$attrs\"><slot /><slot name=\"footer\" /></div>",
});

const ElementInputStub = defineComponent({
  inheritAttrs: false,
  props: {
    modelValue: { type: [String, Number], default: "" },
    type: { type: String, default: "text" },
    size: { type: String, default: undefined },
  },
  emits: ["update:modelValue"],
  template: `
    <textarea
      v-if="type === 'textarea'"
      v-bind="$attrs"
      :value="modelValue"
      @input="$emit('update:modelValue', $event.target.value)"
    />
    <input
      v-else
      v-bind="$attrs"
      :type="type"
      :value="modelValue"
      @input="$emit('update:modelValue', $event.target.value)"
    />
  `,
});

const ElementButtonStub = defineComponent({
  inheritAttrs: false,
  template: `
    <button
      v-bind="$attrs"
      type="button"
    >
      <slot />
    </button>
  `,
});

config.global.stubs = {
  ...config.global.stubs,
  ElAlert: ElementContainerStub,
  ElButton: ElementButtonStub,
  ElCheckbox: ElementModelContainerStub,
  ElCheckboxGroup: ElementModelContainerStub,
  ElDatePicker: ElementModelContainerStub,
  ElDescriptions: ElementContainerStub,
  ElDescriptionsItem: ElementContainerStub,
  ElDialog: ElementModelContainerStub,
  ElDivider: ElementContainerStub,
  ElDrawer: ElementModelContainerStub,
  ElEmpty: ElementContainerStub,
  ElForm: ElementContainerStub,
  ElFormItem: ElementContainerStub,
  ElInput: ElementInputStub,
  ElInputNumber: ElementInputStub,
  ElIcon: ElementContainerStub,
  ElOption: ElementContainerStub,
  ElRadio: ElementModelContainerStub,
  ElRadioButton: ElementModelContainerStub,
  ElRadioGroup: ElementModelContainerStub,
  ElResult: ElementContainerStub,
  ElSelect: ElementModelContainerStub,
  ElSwitch: ElementModelContainerStub,
  ElTabPane: ElementContainerStub,
  ElTabs: ElementModelContainerStub,
  ElTag: ElementContainerStub,
  RouterLink: ElementContainerStub,
};
