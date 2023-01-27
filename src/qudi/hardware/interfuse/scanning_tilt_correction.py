from qudi.core.module import Base



class LinearTransformation(object):
    def __init__(self):
        pass
    def transform_to(self, coord):
        # this is a stub function
        return {key: 0.5*val for key, val in coord.items()}

    def transform_from(self, coord):
        # this is a stub function
        return {key: 2*val for key, val in coord.items()}


class TiltCorrectionMixin(Base):

    def __init__(self, *args, **kwargs):
        self.coord_transform = LinearTransformation()
        super().__init__(*args, **kwargs)

    def get_target(self):
        print("Getting target from uncorrected hw")
        target = super().get_target()
        print(f"uncorrected {target} type {type(target)}")

        target_cor = self.coord_transform.transform_from(target)
        print(f"corrected {target_cor}")

        return target_cor